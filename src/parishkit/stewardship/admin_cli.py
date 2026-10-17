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
take ``--watch``. The schedule change commands (``schedule preview`` and
``schedule confirm``, in ``admin_changes``) and ``config request show``
(with ``--watch``) follow (PR 4), then ``task retry`` and the delivery
commands (PR 9, in ``admin_operations``), ``logs list`` and ``logs
export`` (PR 8a, in ``admin_reports``), the export lifecycle (PR 8b, in
``admin_exports``), the aggregate report reads (PR 8c, in
``admin_report_reads``), the digests (PR 8d, in ``admin_digests``), the
Family-level exports (PR 8e, in ``admin_family_exports``), the Family
portal maintenance switch (PR 7a, in ``admin_portal``), ``send
controls`` (PR 7b, in ``admin_delivery_control``), ``report
source-form`` (in ``admin_source_form``), ``report data-to-fix`` (in
``admin_data_to_fix``), ``files list``, ``files
delete`` and ``files rename`` (PR 10, in ``admin_hosted_files``),
``content list``, ``content show``, ``content history``, ``content preview``
and ``content confirm`` (PR 10, in
``admin_content``), ``integration list``, ``integration show``, ``integration status``
and ``integration schedule-preview`` (PR 10, in ``admin_integrations``),
``parish show``, ``ministries list``, ``branding show`` and ``campaign
shares`` (PR 10, in ``admin_settings``), ``parish preview`` and
``parish confirm`` (PR 10, in ``admin_parish``), and ``events list`` and
``events acknowledge`` (PR 11, in ``admin_events``). A
command that streams a file (``logs export``, ``export census``,
``export limitations``, ``export roster`` and ``export download
--stream``) writes the file's bytes, and nothing else, to standard output,
and its document to standard error. Other areas join the same
subparser tree in later pull requests, each listed in the catalog with the
pull request that added it. ``report roster`` (in ``admin_roster``) counts
the Roster changes to enter list, whose download is ``export roster`` (in
``admin_roster_export``), and ``report census`` (in
``admin_census``) the Census changes list (#463), whose download is
``export census`` (in ``admin_census_export``). ``export limitations``
(in ``admin_limitations_export``) is the Limitations report's download.
``artwork show``,
``artwork remove-preview`` and ``artwork remove`` (PR 10, in
``admin_artwork``) read Campaign images and remove one of them.
``workgroup show``, ``workgroup preview`` and ``workgroup confirm`` (PR 10,
in ``admin_workgroup``) read and change the No-reminders WorkGroup.
``campaign show``, ``campaign preview`` and ``campaign confirm`` (PR 10, in
``admin_campaign``) read and change Campaign settings.
``campaign shares-preview`` and ``campaign shares-confirm`` (PR 10, in
``admin_shares``) change a campaign's Share options.
``followup list``, ``followup show``, ``followup update`` and ``followup
bulk`` (PR 11a, in ``admin_followup``) read and record Ministry follow-up.
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
from uuid import uuid4

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
    """A keyring this process loaded differs from the running web's (exit 2)."""


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


class ConfirmationRequired(Exception):
    """The confirmation was not given at the prompt; nothing changed (exit 4)."""

    code = "confirmation_required"


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
        "The web's keys differ from this command's; retry after the web "
        "service is recreated."
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
    # For a watch: the parsed argument naming the one record it follows (a
    # UUID), logged with a watch timeout (#807); None when it follows no
    # single record.
    watch_subject: str | None = None
    # Whether it streams a file: the handler leaves the bytes in
    # context["stream"]; they alone go to standard output, and every
    # document (success or failure) to standard error.
    streams: bool = False
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
    # The read limits of ``export download``'s campaign read guard: the
    # deployment's download lifetime (``read_guards.ReadLimits``); None
    # uses the default lifetime.
    read_limits: object = None
    # ``keyrings(names)`` loads the named web keyrings for one command that
    # needs them (``load_keyrings``); None in a process that loads none.
    keyrings: object = None

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
    download pool, worker or provider client is assembled. The reports
    root is the web's, so ``export download`` reads the stored file the
    page streams. Returns the receipt of the signing keyring this process
    loaded.
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
    values["STEWARDSHIP_REPORTS_ROOT"] = configuration.paths["reports"]
    values.update(profile_settings(configuration))
    settings.configure(**values)
    django.setup()
    return credential_receipt(loaded, "django_signing")


# The keyrings a Family email's preparation seals with, as the web's
# ``delivery_views._retry_inputs`` passes them (``delivery resolve`` loads
# them only to retry a Family email, #682): never the code MAC ring, which
# preparation does not use, nor the private token ring the web never holds.
FAMILY_KEYRINGS = ("general_encryption", "token_public")


def load_keyrings(configuration, receipts, names):
    """Read the named web keyrings for the one command that needs them.

    Only the command that uses a keyring loads it, and only once it knows
    the request is new, so no other command holds this key material. Each
    file is read once and its credential receipt must equal the one the
    running web published (``receipts``), as for the signing keyring: a
    difference, for example during a key rotation, is
    ``credential_mismatch`` (exit 2) before anything changes. So is a ring
    file that cannot be read or parsed: the web loaded its rings at
    startup, so this command's view of them differs. Returns the parsed
    keyrings in ``names`` order.
    """
    from .accounts.cryptography import CryptographicError, independent_keyrings
    from .accounts.key_files import parse_keyring, read_private
    from .accounts.metrics_credentials import credential_receipt

    rings = []
    try:
        for name in names:
            if name not in configuration.secrets:
                raise CredentialMismatch("The web keyrings are not mounted.")
            loaded = read_private(configuration.secrets[name])
            if credential_receipt(loaded, name) != receipts.get(name):
                raise CredentialMismatch("The web keyrings differ.")
            rings.append(parse_keyring(loaded, name))
        independent_keyrings(*rings)
    except CryptographicError:
        raise CredentialMismatch("The web keyrings cannot be read.") from None
    return tuple(rings)


@contextlib.contextmanager
def admitted(configuration):
    """Admit this process as ``engagement-backfill`` does, then yield its runtime.

    The admitted web profile, the lifecycle mounts, a non-offline startup
    lease and the web's own SQL login; every other profile and login is
    refused. The signing keyring must match the running web's published
    receipt, so a key rotation in progress refuses (exit 2); any other
    keyring loads later, only for the command that needs it, under the same
    check (``load_keyrings``). At most one database connection is held,
    and it is closed at exit.
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
            receipts = loaded_service_receipts(configuration)
            if receipts.get("django_signing") != receipt:
                raise CredentialMismatch("The web signing keyring differs.")
            client = valkey_client(configuration)
            yield AdminRuntime(
                store=AuthorityStore(
                    configuration.paths["authority"], validate_sections
                ),
                pairing=PairingStore(client),
                public_origin=configuration.public_origin,
                setup_complete=setup_is_complete,
                read_limits=download_limits(configuration.runtime_budget),
                keyrings=lambda names: load_keyrings(configuration, receipts, names),
            )
        finally:
            connections.close_all()
            if client is not None:
                client.connection_pool.disconnect()


def download_limits(budget=None):
    """The read limits of ``export download``: the web download's lifetime.

    The page streams a file on the download pool with the deployment's
    ``download_seconds``; the command has no download pool (it reads on its
    own web connection, without claiming one of the web's download slots),
    so its guard's lifetime is that same budget, within the guard's
    600-second maximum for a read without a pool. ``budget`` is the
    deployment's ``RuntimeBudget``; None uses the default one.
    """
    from .campaigns.read_guards import ReadLimits
    from .runtime_budget import RuntimeBudget

    budget = budget or RuntimeBudget()
    return ReadLimits(
        interactive_seconds=min(budget.download_seconds, 600),
        download_seconds=budget.download_seconds,
        download_idle_seconds=budget.download_idle_seconds,
        drain_seconds=budget.drain_seconds,
    )


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
    """List this Administrator's sessions, as Automation access orders them.

    Live sessions only unless ``--include-ended``, which adds those that
    ended in the last 30 days; ``--sort`` takes the page's own tokens
    (``OWN_SORTING``, newest approval first by default), and an unknown
    token is refused as invalid (#621).
    """
    from .accounts.automation_sessions import OWN_SORTING, database_now, own_sessions

    caller = context["caller"]
    sort = OWN_SORTING.parse({} if args.sort is None else {"sort": args.sort})
    rows = own_sessions(
        caller.principal.identity, database_now(), include_ended=args.include_ended
    )
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
                "ended_at": iso(row["ended_at"]),
                "end_reason": row["end_reason"],
                "current": row["id"] == caller.automation_session_id,
            }
            for row in OWN_SORTING.sort_rows(rows, sort)
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


def send_controls(args, preamble, runtime, context):
    """The Pause and resume mail page's status of the current campaign (PR 7b)."""
    from .admin_delivery_control import read_controls

    return read_controls(context["caller"], runtime)


# --- Pause and resume mail's controls (PR 7b, #463) ---------------------------


def _send_reason(args, context):
    """The ``--reason`` text, or for ``-`` standard input (bounded in bytes)."""
    from .accounts.delivery_control_commands import REASON_LIMIT

    # Up to four UTF-8 bytes a character; the page counts characters.
    return _input(args.reason, context, REASON_LIMIT * 4)


def send_pause_preview(args, preamble, runtime, context):
    """Review pausing live Family email, as the page's Preview pause."""
    from .admin_delivery_control import preview_control

    return preview_control(
        context["caller"], runtime, "pause", reason=_send_reason(args, context)
    )


def send_resume_preview(args, preamble, runtime, context):
    """Review resuming live Family email, as the page's Preview resume."""
    from .admin_delivery_control import preview_control

    return preview_control(
        context["caller"], runtime, "resume", reason=_send_reason(args, context)
    )


def send_resolve_preview(args, preamble, runtime, context):
    """Review resolving a closed campaign's held messages, as the page's form.

    As the form: Release and Cancel take at least one message type, Clear
    takes none. The page's own preview checks the rest.
    """
    from .admin_delivery_control import preview_control

    types = sorted(set(args.type or ()))
    if (args.decision == "clear") == bool(types):
        raise UsageError("--type goes with release or cancel, and only with them.")
    return preview_control(
        context["caller"],
        runtime,
        "resolve",
        reason=_send_reason(args, context),
        decision=args.decision,
        types=types,
    )


def send_confirm(args, preamble, runtime, context):
    """Confirm a reviewed pause, resume or resolution, as the page's Confirm.

    The page needs a recent sign-in, so this always prompts (``--yes``
    answers it) with the preview's action, reason, counts and caution,
    after checking the Administrator role and then the token's signature,
    and before any transaction.
    """
    from .admin_changes import TOKEN_LIMIT
    from .admin_delivery_control import confirm_control, confirm_prompt_lines

    # The role first (#1339): Staff is denied before any read or prompt.
    _role_first(context, runtime)
    token = _input(args.token, context, TOKEN_LIMIT)
    confirm(context, confirm_prompt_lines(token))
    return confirm_control(context["caller"], runtime, token=token, context=context)


# --- end of Pause and resume mail's controls ----------------------------------


def system_health(args, preamble, runtime, context):
    """The System health page's problems and panels (ADM-13)."""
    from .admin_reads import read_system_health

    return read_system_health(context["caller"], runtime, audit=context["audit"])


def send_history(args, preamble, runtime, context):
    """One page of the current campaign's Family email history."""
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


# The answer read at a prompt: one line of at most this many bytes.
ANSWER_LIMIT = 256


def confirm(context, summary, *, typed="yes"):
    """Ask for the confirmation the page asks for, or take ``--yes``.

    ``summary`` is the preview's lines (campaign, counts, what cannot be
    undone), written to standard error with the question; never secrets or
    Family data. The answer is one line read from standard input after the
    preamble; anything but ``typed`` (``yes``, or the page's typed value such
    as ``Production``), or end of input, raises ``ConfirmationRequired``
    (exit 4) and nothing changes. ``--yes`` answers it, as a person typing
    the value would. The structured log records which (``confirmation``:
    ``prompt`` or ``yes``); the audit trail does not. Call it before the
    action's transaction opens: no lock may be held while a person reads
    the summary and types. Through the host
    wrapper, a command's answer comes from a terminal only: without one its
    input ends after the preamble and the prompt fails at once.
    """
    from .observability import Event, emit

    stderr = context["stderr"]
    if context["yes"]:
        emit(Event.TASK_STARTED, confirmation="yes")
        return
    for line in summary:
        print(line, file=stderr)
    print(f"Type {typed} to continue: ", end="", file=stderr, flush=True)
    raw = context["stdin"].readline(ANSWER_LIMIT + 1)
    try:
        # Only the line ending and surrounding ASCII spaces are trimmed; the
        # value is then compared exactly.
        answer = raw.decode("utf-8").rstrip("\r\n").strip(" ")
    except UnicodeDecodeError:
        answer = None
    if len(raw) > ANSWER_LIMIT or answer != typed:
        print("", file=stderr)
        raise ConfirmationRequired("The confirmation was not given.")
    emit(Event.TASK_STARTED, confirmation="prompt")


def _input(value, context, limit, *, strip=True):
    """An option's value, or for ``-`` the rest of standard input.

    The preamble was already read; the wrapper forwards what follows it only
    for a ``-`` input. More than ``limit`` bytes, or text that is not UTF-8,
    is invalid input (exit 1), never echoed. Surrounding whitespace is
    trimmed unless ``strip`` is false, for input whose every character
    counts (``content preview --html -``, which must answer as the page).
    """
    if value != "-":
        return value
    raw = context["stdin"].read(limit + 1)
    if len(raw) > limit:
        raise ValueError("The input is too long.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("The input is not UTF-8.") from None
    return text.strip() if strip else text


def _role_first(context, runtime, capability=None):
    """Check the command's capability first, renewing nothing (#1339, #1341).

    A prompting or confirming command calls this as its first step, before
    it reads any input, looks anything up or prompts, so a session whose
    user lacks the capability (an Administrator who is now Staff, say) is
    ``denied`` and sees no prompt. ``capability`` is the one the action's
    own admission checks (``CONFIGURE``, an Administrator's, by default), so
    a command Staff may run stays Staff's; the action still admits as its
    page's post does.
    """
    from .admin_changes import _admit_change

    _admit_change(context["caller"], runtime, read_only=True, capability=capability)


def schedule_preview(args, preamble, runtime, context):
    """Review a change to mail schedules or campaign dates (PR 4)."""
    from .admin_changes import CHANGES_LIMIT, preview_schedule

    return preview_schedule(
        context["caller"],
        runtime,
        args.campaign,
        expected_version=args.expected_version,
        changes=_input(args.changes, context, CHANGES_LIMIT),
    )


def schedule_confirm(args, preamble, runtime, context):
    """Confirm a reviewed schedule change as a configuration request (PR 4)."""
    from .admin_changes import TOKEN_LIMIT, confirm_schedule

    return confirm_schedule(
        context["caller"],
        runtime,
        args.campaign,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def config_request_show(args, preamble, runtime, context):
    """The status of one of this Administrator's configuration requests (PR 4)."""
    from .admin_reads import read_config_request

    return read_config_request(context["caller"], runtime, args.request_id)


def config_request_cancel(args, preamble, runtime, context):
    """Cancel a live end-date change that has not applied (#944).

    The reason is read first, so a refused prompt has nothing left to read;
    the prompt comes before any transaction opens.
    """
    from .admin_changes import REASON_LIMIT, cancel_end_change

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    reason = _input(args.reason, context, REASON_LIMIT * 4)
    confirm(
        context,
        (
            f"Cancel configuration request {args.request_id}, a live campaign's "
            "end-date change that has not applied.",
            "The previous end date stays; the change cannot be resumed.",
        ),
    )
    return cancel_end_change(
        context["caller"], runtime, args.request_id, reason=reason, context=context
    )


def task_retry(args, preamble, runtime, context):
    """Retry a failed background task as its page's Retry button does (PR 9).

    Without ``--request-key`` a new key is made and written to standard error
    before anything is done, so a run that crashes can be repeated with it.
    """
    from .admin_operations import retry_task

    return retry_task(
        context["caller"],
        runtime,
        args.task_id,
        request_key=_request_key(args, context),
        context=context,
    )


def _request_key(args, context):
    """``--request-key``, or a new key written to standard error before acting.

    The key is written first so a run that crashes can be repeated with it.
    """
    from uuid import uuid4

    key = args.request_key
    if key is None:
        key = uuid4()
        print(
            f"pk-admin: request key {key}; repeat with --request-key {key} "
            "to retry safely.",
            file=context["stderr"],
            flush=True,
        )
    return key


def delivery_list(args, preamble, runtime, context):
    """One page of Outgoing mail, filtered and sorted as the page (PR 9)."""
    from .admin_operations import read_deliveries
    from .admin_reads import query

    parameters = query(
        state=args.state,
        send=args.send,
        q=args.search,
        page=args.page,
        size=args.size,
        sort=args.sort,
    )
    return read_deliveries(context["caller"], runtime, parameters)


def delivery_show(args, preamble, runtime, context):
    """One delivery, its history, notes and the resolutions offered (PR 9)."""
    from .admin_operations import read_delivery
    from .admin_reads import query

    parameters = query(page=args.page, size=args.size)
    return read_delivery(context["caller"], runtime, args.message_id, parameters)


def delivery_refusals(args, preamble, runtime, context):
    """One page of unresolved refused addresses (PR 9)."""
    from .admin_operations import read_refusal_list
    from .admin_reads import query

    parameters = query(duid=args.duid, page=args.page, size=args.size, sort=args.sort)
    return read_refusal_list(context["caller"], runtime, parameters)


def delivery_refusal_show(args, preamble, runtime, context):
    """One refused address's record and the source version to verify (PR 9)."""
    from .admin_operations import read_refusal_detail

    return read_refusal_detail(context["caller"], runtime, args.refusal_id)


def delivery_resolve(args, preamble, runtime, context):
    """Resolve one delivery as its page's form does (PR 9)."""
    from .admin_operations import NOTE_LIMIT, resolve_delivery_command

    key = _request_key(args, context)
    return resolve_delivery_command(
        context["caller"],
        runtime,
        args.message_id,
        action=args.action,
        expected_version=args.expected_version,
        note=_input(args.note, context, NOTE_LIMIT * 4),
        request_key=key,
        context=context,
    )


def refresh_status(args, preamble, runtime, context):
    """The Refresh from ParishSoft page's read (PR 6a).

    Pending work and the latest refresh.
    """
    from .admin_refresh import read_refresh_status

    return read_refresh_status(context["caller"], runtime)


def refresh_start(args, preamble, runtime, context):
    """Ask for a full refresh as the Refresh from ParishSoft page does (PR 6a).

    Without ``--request-key`` a new key is made and written to standard error
    before anything is done, so a run that crashes can be repeated with it.
    """
    from uuid import uuid4

    from .admin_refresh import start_refresh

    key = args.request_key
    if key is None:
        key = uuid4()
        print(
            f"pk-admin: request key {key}; repeat with --request-key {key} "
            "to request safely.",
            file=context["stderr"],
            flush=True,
        )
    return start_refresh(context["caller"], runtime, request_key=key, context=context)


def sample_preview(args, preamble, runtime, context):
    """Review a sample test email, as its page does (PR 6b)."""
    from uuid import uuid4

    from .admin_tests import preview_sample

    return preview_sample(
        context["caller"],
        runtime,
        args.revision_id,
        request_key=args.request_key or uuid4(),
    )


def sample_test(args, preamble, runtime, context):
    """Send a reviewed sample test email, as its page's button does (PR 6b).

    The page's acknowledgement is asked only while an earlier test's outcome
    is unknown, as the page shows its checkbox only then, and before any
    transaction opens.
    """
    from .admin_changes import TOKEN_LIMIT
    from .admin_tests import UNKNOWN_ACKNOWLEDGEMENT, send_sample, unknown_outcome

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    token = _input(args.token, context, TOKEN_LIMIT)
    unknown = unknown_outcome(context["caller"], runtime)
    if unknown:
        confirm(
            context,
            (
                "An earlier test email's outcome is unknown.",
                UNKNOWN_ACKNOWLEDGEMENT,
            ),
        )
    return send_sample(
        context["caller"],
        runtime,
        token=token,
        acknowledge_unknown=unknown,
        context=context,
    )


def families_preview(args, preamble, runtime, context):
    """Review a chosen-Family test, as its page does (PR 6c).

    With ``--names`` (and optionally ``--format`` and ``--timezone``) it
    also requests the export of the names the page shows beside the DUIDs
    (#817), as CSV, XLSX or PDF (#966). That changes state, so without
    ``--request-key`` the new key is written to standard error first, so a
    run that crashes can be repeated with it. The file has no times in its
    rows, so no zone is needed (#1127): without ``--timezone`` its frame
    times use the parish's time zone, as a host command has no browser.
    """
    from uuid import uuid4

    from .admin_tests import preview_families, preview_families_with_names

    if args.timezone is not None and not args.names:
        raise UsageError("--timezone goes with --names.")
    if args.format is not None and not args.names:
        raise UsageError("--format goes with --names.")
    if args.names:
        return preview_families_with_names(
            context["caller"],
            runtime,
            args.revision_id,
            args.family,
            request_key=_request_key(args, context),
            zone=args.timezone,
            format=args.format or DEFAULT_EXPORT_FORMAT,
            context=context,
        )
    return preview_families(
        context["caller"],
        runtime,
        args.revision_id,
        args.family,
        request_key=args.request_key or uuid4(),
    )


def families_test(args, preamble, runtime, context):
    """Send a reviewed chosen-Family test, as its page's confirmation does (PR 6c).

    The page always asks for its acknowledgement, so this always prompts,
    before any transaction opens.
    """
    from .admin_changes import TOKEN_LIMIT
    from .admin_tests import FAMILIES_ACKNOWLEDGEMENT, families_count, send_families

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    token = _input(args.token, context, TOKEN_LIMIT)
    count = families_count(token)
    confirm(
        context,
        (
            "Send a chosen-Family test of "
            + ("the reviewed Families" if count is None else f"{count} Families")
            + " to the Testing recipient.",
            FAMILIES_ACKNOWLEDGEMENT,
        ),
    )
    return send_families(context["caller"], runtime, token=token, context=context)


def families_status(args, preamble, runtime, context):
    """The page's Recent Family tests, without DUIDs (PR 6c)."""
    from .admin_tests import read_families_status

    return read_families_status(context["caller"], runtime)


def portal_maintenance_show(args, preamble, runtime, context):
    """Whether the Family portal is closed for maintenance, as its page (PR 7a)."""
    from .admin_portal import read_maintenance

    return read_maintenance(context["caller"], runtime)


def content_list(args, preamble, runtime, context):
    """The Pages and emails page's list for the current campaign (PR 10)."""
    from .admin_content import read_content_list

    return read_content_list(context["caller"], runtime)


def content_show(args, preamble, runtime, context):
    """The content editor as it opens: one page's or email's text (PR 10)."""
    from .admin_content import read_content_show

    return read_content_show(
        context["caller"],
        runtime,
        args.kind,
        args.slot,
        args.revision,
        args.default,
    )


def content_preview(args, preamble, runtime, context):
    """Build a Pages and emails Delete, or preview typed HTML's plain text (PR 10).

    ``--delete`` (repeatable) needs ``--expected-version`` and ``--html``
    takes none (``main`` checks both before admission). The role is
    checked first (#1341), before any input is read.
    """
    from .admin_content import (
        admit_preview,
        plain_text_preview,
        preview_deletion,
    )
    from .web.content import MAX_TEXT_BYTES

    delete = args.delete is not None
    actor = admit_preview(context["caller"], runtime, delete=delete)
    if not delete:
        # Unstripped: the page converts the posted HTML exactly as typed.
        return plain_text_preview(
            _input(args.html, context, MAX_TEXT_BYTES, strip=False)
        )
    return preview_deletion(
        context["caller"],
        runtime,
        actor,
        args.delete,
        expected_version=args.expected_version,
    )


def content_confirm(args, preamble, runtime, context):
    """Confirm a built Pages and emails Delete as a configuration request (PR 10).

    The role is checked first (#1341), before the token is read.
    """
    from .admin_changes import TOKEN_LIMIT, _admit_change
    from .admin_content import confirm_content

    actor = _admit_change(context["caller"], runtime)
    return confirm_content(
        context["caller"],
        runtime,
        actor,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def content_history(args, preamble, runtime, context):
    """The Content history page, and one revision's sample (PR 10)."""
    from .admin_content import read_content_history

    return read_content_history(context["caller"], runtime, args.revision)


def integration_list(args, preamble, runtime, context):
    """The Integrations page: each integration and whether it is set up (PR 10)."""
    from .admin_integrations import read_integration_list

    return read_integration_list(context["caller"], runtime)


def integration_show(args, preamble, runtime, context):
    """One integration's settings page, as it opens (PR 10)."""
    from .admin_integrations import read_integration_show

    return read_integration_show(context["caller"], runtime, args.target)


def integration_status(args, preamble, runtime, context):
    """The status line an integration's settings page follows (PR 10)."""
    from .admin_integrations import read_integration_status

    return read_integration_status(context["caller"], runtime, args.target)


def integration_schedule_preview(args, preamble, runtime, context):
    """Preview the applied or a proposed ParishSoft refresh schedule (#632).

    The role first (#1339): Staff is denied before standard input is read.
    """
    from .admin_integration_changes import CHANGES_LIMIT
    from .admin_integrations import read_schedule_preview

    _role_first(context, runtime)
    schedule = args.refresh_schedule
    return read_schedule_preview(
        context["caller"],
        runtime,
        None if schedule is None else _input(schedule, context, CHANGES_LIMIT),
    )


def integration_preview(args, preamble, runtime, context):
    """Review a change to, or the removal of, an integration's settings (PR 10).

    ``--refresh-schedule`` (ParishSoft's refresh schedule, #632) goes with
    or instead of ``--changes``, never with ``--remove``; only one of them
    may read standard input.
    """
    from .admin_integration_changes import CHANGES_LIMIT, preview_integration

    schedule = args.refresh_schedule
    if args.remove and schedule is not None:
        raise UsageError("--refresh-schedule does not go with --remove.")
    if not args.remove and args.changes is None and schedule is None:
        raise UsageError("Give --changes, --refresh-schedule or --remove.")
    if args.changes == "-" and schedule == "-":
        raise UsageError("Only one input may be read from standard input.")
    return preview_integration(
        context["caller"],
        runtime,
        args.target,
        expected_version=args.expected_version,
        changes=(
            None
            if args.changes is None
            else _input(args.changes, context, CHANGES_LIMIT)
        ),
        remove=args.remove,
        refresh_schedule=(
            None if schedule is None else _input(schedule, context, CHANGES_LIMIT)
        ),
    )


def integration_confirm(args, preamble, runtime, context):
    """Apply a reviewed integration settings change (PR 10).

    The page's Apply needs a recent sign-in (#547), so this always prompts,
    naming the integration and the settings the review changes (never their
    values), after checking the Administrator role and then the token's
    signature, and before any transaction.
    """
    from .admin_changes import TOKEN_LIMIT
    from .admin_integration_changes import (
        applied_settings,
        confirm_integration,
        prompt_lines,
    )

    # The role first (#1339): Staff is denied before any read or prompt
    # could say whether the integration has a reviewed change.
    _role_first(context, runtime)
    token = _input(args.token, context, TOKEN_LIMIT)
    applied = applied_settings(runtime, args.target)
    confirm(context, prompt_lines(args.target, token, applied))
    return confirm_integration(
        context["caller"], runtime, args.target, token=token, context=context
    )


def integration_backup_probe(args, preamble, runtime, context):
    """Queue the Off-site backups page's Test access check (PR 10)."""
    from .admin_integration_actions import backup_probe

    return backup_probe(context["caller"], runtime, folder=args.folder, context=context)


def integration_dismiss(args, preamble, runtime, context):
    """Dismiss a finished key change's status line (PR 10)."""
    from .admin_integration_actions import dismiss_key

    return dismiss_key(
        context["caller"],
        runtime,
        args.target,
        request_id=args.request_id,
        context=context,
    )


def integration_key_status(args, preamble, runtime, context):
    """One of the Administrator's own key changes, as its status page (PR 10).

    Without ``--request-id`` the first read finds the latest one; a watch
    then keeps following that key change (and logs it as the watch's
    subject), even if a newer one starts meanwhile.
    """
    from uuid import UUID

    from .admin_integration_keys import read_key_status

    model = read_key_status(
        context["caller"], runtime, args.target, request_id=args.request_id
    )
    if args.request_id is None:
        args.request_id = UUID(model.request_id)
    return model


def integration_key_finish_switching_preview(args, preamble, runtime, context):
    """Review Finish switching to an integration's new key (PR 10)."""
    from .admin_integration_keys import preview_finish

    return preview_finish(context["caller"], runtime, args.target)


def integration_key_finish_switching(args, preamble, runtime, context):
    """Finish switching an integration to its new key (PR 10).

    The page needs a recent sign-in (#547), so this always prompts, naming
    the integration and the settings the switch also changes (never their
    values), after checking the Administrator role and then the token's
    signature, and before any transaction.
    """
    from .admin_changes import TOKEN_LIMIT
    from .admin_integration_keys import confirm_finish, finish_prompt_lines

    # The role first (#1339): Staff is denied before any read or prompt
    # could say whether the integration has a key change.
    _role_first(context, runtime)
    token = _input(args.token, context, TOKEN_LIMIT)
    confirm(context, finish_prompt_lines(runtime, args.target, token))
    return confirm_finish(
        context["caller"], runtime, args.target, token=token, context=context
    )


def parish_show(args, preamble, runtime, context):
    """The Parish settings page's profile (PR 10)."""
    from .admin_settings import read_parish

    return read_parish(context["caller"], runtime)


def parish_preview(args, preamble, runtime, context):
    """Review a change to the parish profile (PR 10)."""
    from .admin_parish import CHANGES_LIMIT, preview_parish

    return preview_parish(
        context["caller"],
        runtime,
        expected_version=args.expected_version,
        changes=_input(args.changes, context, CHANGES_LIMIT),
    )


def parish_confirm(args, preamble, runtime, context):
    """Confirm a reviewed parish profile change as a configuration request (PR 10)."""
    from .admin_parish import TOKEN_LIMIT, confirm_parish

    return confirm_parish(
        context["caller"],
        runtime,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def workgroup_show(args, preamble, runtime, context):
    """The current campaign's No-reminders WorkGroup (PR 10)."""
    from .admin_workgroup import read_workgroup

    return read_workgroup(context["caller"], runtime)


def workgroup_preview(args, preamble, runtime, context):
    """Review a change to the No-reminders WorkGroup (PR 10)."""
    from .admin_workgroup import preview_workgroup

    return preview_workgroup(
        context["caller"],
        runtime,
        name=None if args.clear else args.name,
        expected_version=args.expected_version,
    )


def workgroup_confirm(args, preamble, runtime, context):
    """Confirm a reviewed No-reminders WorkGroup change as a request (PR 10)."""
    from .admin_changes import TOKEN_LIMIT
    from .admin_workgroup import confirm_workgroup

    return confirm_workgroup(
        context["caller"],
        runtime,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def campaign_show(args, preamble, runtime, context):
    """Campaign settings: the current campaign's settings (PR 10)."""
    from .admin_campaign import read_campaign

    return read_campaign(context["caller"], runtime)


def campaign_preview(args, preamble, runtime, context):
    """Review a change to the current campaign's settings (PR 10)."""
    from .admin_campaign import CHANGES_LIMIT, preview_campaign

    # The role first (#1341): denied before any input is read.
    _role_first(context, runtime)
    return preview_campaign(
        context["caller"],
        runtime,
        expected_version=args.expected_version,
        changes=_input(args.changes, context, CHANGES_LIMIT),
    )


def campaign_confirm(args, preamble, runtime, context):
    """Confirm a reviewed Campaign settings change as a request (PR 10)."""
    from .admin_campaign import confirm_campaign
    from .admin_changes import TOKEN_LIMIT

    # The role first (#1341): denied before any input is read.
    _role_first(context, runtime)
    return confirm_campaign(
        context["caller"],
        runtime,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def ministries_list(args, preamble, runtime, context):
    """The Ministries page's catalog, every row (PR 10)."""
    from .admin_settings import read_ministries

    return read_ministries(context["caller"], runtime)


def branding_show(args, preamble, runtime, context):
    """The current parish logos Parish logos draws (PR 10)."""
    from .admin_settings import read_branding

    return read_branding(context["caller"], runtime)


def campaign_shares(args, preamble, runtime, context):
    """A campaign's Share options and whether the page edits them (PR 10)."""
    from .admin_settings import read_shares

    return read_shares(context["caller"], runtime, args.campaign)


def artwork_show(args, preamble, runtime, context):
    """The current campaign's images, as Campaign images draws them (PR 10)."""
    from .admin_artwork import read_artwork

    return read_artwork(context["caller"], runtime)


def events_list(args, preamble, runtime, context):
    """Home's security events and critical-problem banner (PR 11)."""
    from .admin_events import read_events

    return read_events(context["caller"], runtime)


def events_acknowledge(args, preamble, runtime, context):
    """Acknowledge one security event, or the banner rows listed (PR 11).

    Home asks for no confirmation, so this does not prompt.
    """
    from .admin_events import TOKEN_LIMIT, acknowledge

    token = None
    if args.critical is not None:
        token = _input(args.critical, context, TOKEN_LIMIT)
    return acknowledge(
        context["caller"],
        runtime,
        event_id=args.security,
        token=token,
        context=context,
    )


def artwork_remove_preview(args, preamble, runtime, context):
    """Review removing one Campaign image (PR 10)."""
    from .admin_artwork import preview_removal

    return preview_removal(
        context["caller"], runtime, args.slot, expected_version=args.expected_version
    )


def artwork_remove(args, preamble, runtime, context):
    """Remove one Campaign image as a reviewed configuration request (PR 10)."""
    from .admin_artwork import remove_image
    from .admin_changes import TOKEN_LIMIT

    return remove_image(
        context["caller"],
        runtime,
        args.slot,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def files_list(args, preamble, runtime, context):
    """The Hosted files page's library, with each file's uses (PR 10)."""
    from .admin_hosted_files import read_library

    return read_library(context["caller"], runtime)


def files_delete(args, preamble, runtime, context):
    """Delete unused hosted files, as the page's Delete does (PR 10).

    The chosen files are read first, so one in use is refused before the
    prompt, as the page offers Delete only for unused files. A deletion
    cannot be undone, so it prompts, naming the files, before any
    transaction; when none of them is left, there is nothing to ask.
    """
    from .accounts.hosted_file_models import MAX_FILES
    from .admin_hosted_files import delete_files, preview_deletion

    ids = args.file_id
    if len(ids) > MAX_FILES or len(set(ids)) != len(ids):
        raise UsageError(f"Name at most {MAX_FILES} distinct files.")
    found = preview_deletion(context["caller"], runtime, ids)
    if found:
        confirm(
            context,
            (
                f"Delete {len(found)} hosted file(s): "
                + ", ".join(row.slug for row in found)
                + ".",
                "This cannot be undone: their links stop working.",
            ),
        )
    return delete_files(context["caller"], runtime, ids, context=context)


def files_rename(args, preamble, runtime, context):
    """Change an unused hosted file's placeholder name, as the page's Edit does.

    The page asks for no confirmation, and the link never changes, so this
    does not prompt.
    """
    from .admin_hosted_files import rename_file

    return rename_file(
        context["caller"], runtime, args.file_id, args.slug.strip(), context=context
    )


def history_list(args, preamble, runtime, context):
    """The Past campaigns page's imported years and tombstones (#1018)."""
    from .admin_history import list_archives

    return list_archives(context["caller"], runtime)


def history_preview(args, preamble, runtime, context):
    """Review one archive workbook read from standard input (#1018).

    The workbook's bytes follow the preamble (the wrapper forwards them for
    the ``-`` input); nothing else is read. A refused file is ``invalid``
    with its located problems.
    """
    from .admin_history import preview_archive, read_workbook

    data = read_workbook(context["stdin"])
    return preview_archive(
        context["caller"],
        runtime,
        data,
        keep_family_rows=not args.no_family_rows,
        context=context,
    )


def history_confirm(args, preamble, runtime, context):
    """Apply a reviewed archive, as the page's Apply does (#1018).

    A replacement removes the year's earlier import, so it asks the page's
    acknowledgement first, naming the year; an addition asks nothing.
    """
    from .admin_history import confirm_archive, pending_replacement

    pending, current = pending_replacement(context["caller"], runtime, args.token)
    if current is not None:
        confirm(
            context,
            (
                f"This replaces the {pending.campaign_year} figures already "
                f"imported (version {current.version}, "
                f"checksum {current.checksum[:12]}).",
                "The earlier import is removed; its file can be imported again.",
            ),
        )
    return confirm_archive(
        context["caller"],
        runtime,
        args.token,
        replace_acknowledged=current is not None,
        context=context,
    )


def history_remove(args, preamble, runtime, context):
    """Remove one year's imported figures, as the page's Remove does (#1018)."""
    from .admin_history import remove_archive

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    confirm(
        context,
        (
            f"Remove the {args.year} figures imported from a past campaign.",
            "The report stops showing the year; the file can be imported again.",
        ),
    )
    return remove_archive(
        context["caller"],
        runtime,
        args.year,
        expected_version=args.expected_version,
        context=context,
    )


def system_backup_now(args, preamble, runtime, context):
    """Take a backup now, as System health's button does (ADM-13 PR 3b).

    The page needs a recent sign-in, so this always prompts, with the
    page's preview (the last backup and its size, off-site copies,
    retention, a send in progress), before any transaction; a request
    already live or a restore under review is refused before the prompt.
    ``--request-key`` names the request, so a repeat returns the first.
    The session stands in for the page's recent sign-in, as the request's
    guard admits.
    """
    from .system_health import backup_now, backup_preview_lines

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    # A repeat with the key of a request already recorded asks nothing and
    # returns that request, as the page's repeated confirmation does.
    lines = backup_preview_lines(runtime.store, key=args.request_key)
    if lines is not None:
        confirm(context, lines)
    return backup_now(
        context["caller"],
        runtime,
        key=_request_key(args, context),
        context=context,
    )


def system_debug_off(args, preamble, runtime, context):
    """Turn off debug logging, as System health's button does (ADM-13 PR 6b).

    Like ``system backup-now``: it always prompts, with the page's preview
    (the services with debug logging on, what stays in the logs, and that
    only the server operator clears the switch), before any transaction;
    a switch already set is refused before the prompt. ``--request-key``
    names the switch, so a repeat returns the first. The session stands in
    for the page's recent sign-in, as the switch's guard admits.
    """
    from .system_health import debug_off, debug_off_preview_lines

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    lines = debug_off_preview_lines(runtime.store, key=args.request_key)
    if lines is not None:
        confirm(context, lines)
    return debug_off(
        context["caller"],
        runtime,
        key=_request_key(args, context),
        context=context,
    )


def system_refresh_accept_preview(args, preamble, runtime, context):
    """Review accepting the refused ParishSoft change once, as System
    health's Accept this change once preview (ADM-13 PR 5b).

    Prints every count the refusal recorded and ``preview.token``, the
    signed binding ``system refresh-accept`` takes. Changes nothing.
    """
    from .system_health import refresh_accept_preview

    return refresh_accept_preview(context["caller"], runtime)


def system_refresh_accept(args, preamble, runtime, context):
    """Accept the reviewed refused change once, as System health's Confirm
    does (ADM-13 PR 5b), and ask for a full refresh.

    It always prompts with the counts that fell too far and the page's tick
    box ("I checked this in ParishSoft, and the change is real") before any
    transaction, after checking the token. The token's key names the
    acceptance, so a repeat returns the first. The session stands in for
    the page's recent sign-in, as the acceptance's guard admits.
    """
    from .admin_changes import TOKEN_LIMIT
    from .system_health import accept_prompt_lines, refresh_accept

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    token = _input(args.token, context, TOKEN_LIMIT)
    confirm(context, accept_prompt_lines(runtime, token, context["caller"]))
    return refresh_accept(context["caller"], runtime, token=token, context=context)


def system_debug_allow(args, preamble, runtime, context):
    """Allow debug logging again, as System health's button does in Testing
    (ADM-13 PR 6c).

    It always prompts, with the page's preview, before any transaction; in
    Production, or while no switch is set, it is refused before the prompt.
    ``--switch`` names the switch to clear (by default the one set now), so
    a repeat naming a switch this Administrator already cleared returns it.
    The session stands in for the page's recent sign-in, as the switch's
    guard admits.
    """
    from .system_health import debug_allow, debug_allow_preview_lines

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    switch, lines = debug_allow_preview_lines(runtime.store, switch=args.switch)
    if lines is not None:
        confirm(context, lines)
    return debug_allow(context["caller"], runtime, switch=switch, context=context)


def portal_maintenance_set(args, preamble, runtime, context):
    """Close or reopen the Family portal, as its page's buttons do (PR 7a).

    The note is read and checked with the page's own rules first, so a bad
    one is refused before the prompt. The page needs a recent sign-in, so
    this always prompts, with the page's notices, before any transaction.
    """
    from .accounts.family_maintenance import MESSAGE_LIMIT, clean_message
    from .admin_portal import set_maintenance

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    closed = args.state == "closed"
    if args.message is not None and not closed:
        raise UsageError("--message goes with --state closed.")
    message = ""
    if closed:
        # Up to four UTF-8 bytes a character; clean_message counts characters.
        message = clean_message(_input(args.message, context, MESSAGE_LIMIT * 4))
    confirm(
        context,
        (
            "Close the Family portal for maintenance: Families see a "
            "temporarily unavailable page and no Family answers are saved.",
            "After closing, wait about 10 seconds before changing any data.",
            "Closing does not stop email; pause Family email delivery for that.",
        )
        if closed
        else ("Reopen the Family portal: Families can use the form again.",),
    )
    return set_maintenance(
        context["caller"], runtime, closed=closed, message=message, context=context
    )


def _log_filters(args):
    """The System logs page's filter fields from ``logs`` options."""
    return {
        "show": args.show,
        "activity": args.activity,
        "event": args.event,
        "actor": args.actor,
        "correlation": args.correlation,
        "campaign": args.campaign,
        "subject": args.subject,
        "text": args.text,
        "ministry": args.ministry,
        "start": args.start,
        "end": args.end,
        "zone": args.zone,
    }


def logs_list(args, preamble, runtime, context):
    """One page of System logs, filtered as the page (PR 8a)."""
    from .admin_reports import read_logs

    filters = _log_filters(args) | {
        "through": args.through,
        "page": args.page,
        "size": args.size,
        "sort": args.sort,
    }
    return read_logs(context["caller"], runtime, filters)


def logs_export(args, preamble, runtime, context):
    """The System logs page's download, streamed to standard output (PR 8a)."""
    from .admin_reports import export_logs

    return export_logs(
        context["caller"],
        runtime,
        _log_filters(args),
        fmt=args.format,
        zone_name=args.timezone,
        context=context,
    )


def export_create(args, preamble, runtime, context):
    """Request a participation export as the page's export form does (PR 8b)."""
    from .admin_exports import create_export_command

    return create_export_command(
        context["caller"],
        runtime,
        fact_set_id=args.fact_set,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_comparison(args, preamble, runtime, context):
    """Request a Campaign comparison download, as its page's form does (#1018)."""
    from .admin_exports import create_comparison_command

    return create_comparison_command(
        context["caller"],
        runtime,
        day=args.day,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_archive(args, preamble, runtime, context):
    """Request the campaign archive workbook, as Past campaigns does (#1018).

    It holds each Family's giving, so, like ``export financial``, it is a
    fresh-gated action: the command asks at the confirmation prompt (or
    takes ``--yes``) before a request key is made or any transaction opens.
    """
    from .admin_family_exports import export_archive as request

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime)

    confirm(
        context,
        (
            "Create the campaign archive: a workbook of each Family's responses "
            "and giving for the current campaign.",
        ),
    )
    return request(
        context["caller"],
        runtime,
        request_key=_request_key(args, context),
        context=context,
    )


def export_financial(args, preamble, runtime, context):
    """Request a financial report export, as its export form does (PR 8e).

    Creating one is a fresh-gated action (#547), so the command asks at the
    confirmation prompt (or takes ``--yes``) before a request key is made or
    any transaction opens; the session then stands in for the page's recent
    sign-in (``admin_family_exports.export_financial``).
    """
    from .accounts.policy import Capability
    from .admin_family_exports import export_financial as request

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.FINANCIAL_DETAIL)

    confirm(
        context,
        (
            "Create a financial export: a file of Families' giving detail "
            "for the current campaign.",
        ),
    )
    return request(
        context["caller"],
        runtime,
        filters=args.filter,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_information(args, preamble, runtime, context):
    """Request an additional information export, as its form does (PR 8e)."""
    from .admin_family_exports import export_information as request

    return request(
        context["caller"],
        runtime,
        filters=args.filter,
        history=args.history,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_ministry(args, preamble, runtime, context):
    """Request a Ministry report export, as its export form does (PR 8e)."""
    from .admin_family_exports import export_ministry as request

    return request(
        context["caller"],
        runtime,
        filters=args.filter,
        ministry=args.ministry,
        requests=args.requests,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_ministry_packet(args, preamble, runtime, context):
    """Request a Ministry follow-up packet, as its packet form does (PR 8e)."""
    from .admin_family_exports import export_ministry_packet as request

    return request(
        context["caller"],
        runtime,
        ministries=args.ministry,
        history=args.history,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_directory(args, preamble, runtime, context):
    """Request a Family-code directory export, as its form does (PR 8f).

    Fresh-gated (#547), so it asks at the confirmation prompt first, as
    ``export financial`` does.
    """
    from .accounts.policy import Capability
    from .admin_family_exports import export_directory as request

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.FAMILY_CODES)

    confirm(
        context,
        (
            "Create an Active parishioner family directory export: a file of "
            "every matching Family's code for the current campaign.",
        ),
    )
    return request(
        context["caller"],
        runtime,
        filters=args.filter,
        postal=False,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_postal(args, preamble, runtime, context):
    """Request a postal mail-merge export, as its form does (PR 8f).

    Like the page, it lists only the Families postal invitations are for:
    reach is always ``mail`` (#951; ``DirectoryQuery.postal``), so a
    ``--filter reach=`` asking for any other list (``email`` or
    ``neither``) is a usage error rather than silently replaced.

    Fresh-gated (#547), so it asks at the confirmation prompt first, as
    ``export financial`` does.
    """
    from .accounts.policy import Capability
    from .admin_family_exports import export_directory as request

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.FAMILY_CODES)

    for pair in args.filter or ():
        name, _, value = pair.partition("=")
        if name == "reach" and value not in {"mail", "any"}:
            raise UsageError(
                "export postal lists only Families reached by postal mail; "
                "--filter reach may be only mail or any."
            )
    confirm(
        context,
        (
            "Create a mail-merge export of the active Families that get "
            "their invitation by postal mail (no deliverable head email, "
            "a usable mailing address) and match the filters: each one's "
            "code and mailing address for the current campaign.",
        ),
    )
    return request(
        context["caller"],
        runtime,
        filters=args.filter,
        postal=True,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_exact_create(args, preamble, runtime, context):
    """Request an exact daily export, as the page's form does (PR 8h)."""
    from .admin_exact_exports import create_exact

    return create_exact(
        context["caller"],
        runtime,
        scope=args.scope,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_exact_status(args, preamble, runtime, context):
    """One exact daily export's state, as its status page shows it (PR 8h)."""
    from .admin_exact_exports import read_exact

    return read_exact(context["caller"], runtime, args.exact_id)


def export_exact_cancel(args, preamble, runtime, context):
    """Cancel an exact daily export, as its page's Cancel does (PR 8h)."""
    from .admin_exact_exports import cancel_exact

    return cancel_exact(context["caller"], runtime, args.exact_id, context=context)


def export_exact_retry(args, preamble, runtime, context):
    """Retry an exact daily export, as its page's Retry does (PR 8h)."""
    from .admin_exact_exports import retry_exact

    return retry_exact(
        context["caller"],
        runtime,
        args.exact_id,
        request_key=_request_key(args, context),
        context=context,
    )


def export_family_timeline(args, preamble, runtime, context):
    """Request one Family's timeline export, as its page's form does (PR 8g).

    The file can hold the Family's live code, so it is fresh-gated (#547)
    and asks at the confirmation prompt first, as ``export directory`` does.
    """
    from .accounts.policy import Capability
    from .admin_family_exports import export_family_timeline as request

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.CAMPAIGN_REPORT)

    confirm(
        context,
        ("Create a Family timeline export, which can hold the Family's code.",),
    )
    return request(
        context["caller"],
        runtime,
        family_id=args.family_id,
        mode=args.mode,
        fmt=args.format,
        zone=args.timezone,
        request_key=_request_key(args, context),
        context=context,
    )


def export_status(args, preamble, runtime, context):
    """One export's state, as its status page shows it (PR 8b)."""
    from .admin_exports import read_export

    return read_export(context["caller"], runtime, args.export_id)


def export_cancel(args, preamble, runtime, context):
    """Cancel an export that is not published yet, as its page does (PR 8b)."""
    from .admin_exports import cancel_export_command

    return cancel_export_command(
        context["caller"], runtime, args.export_id, context=context
    )


def export_retry(args, preamble, runtime, context):
    """Retry an export whose latest run failed, as its page does (PR 8b)."""
    from .admin_exports import retry_export_command

    return retry_export_command(
        context["caller"],
        runtime,
        args.export_id,
        request_key=_request_key(args, context),
        context=context,
    )


def export_regenerate(args, preamble, runtime, context):
    """Request an expired export again, as its page's Regenerate does (PR 8b).

    Regenerating a directory, mail-merge or financial export is fresh-gated
    (#547), so for those, and only those, the command asks at the
    confirmation prompt (or takes ``--yes``) before a request key is made or
    any transaction opens.
    """
    from .admin_exports import regenerate_export_command, regeneration_prompts

    if regeneration_prompts(context["caller"], runtime, args.export_id):
        confirm(
            context,
            (
                "Regenerate an export of Family codes or financial detail: "
                "a new copy of its file.",
            ),
        )
    return regenerate_export_command(
        context["caller"],
        runtime,
        args.export_id,
        request_key=_request_key(args, context),
        context=context,
    )


def export_download(args, preamble, runtime, context):
    """Stream an export's stored file to standard output (PR 8b).

    The bytes are written as they are read, inside the page's campaign read
    guard, so nothing is kept in memory or on disk on the way.
    """
    from .admin_exports import download_export

    stdout = context["stdout"]
    return download_export(
        context["caller"],
        runtime,
        args.export_id,
        write=lambda chunk: write_stream(stdout, chunk),
        limits=runtime.read_limits or download_limits(),
    )


def report_list(args, preamble, runtime, context):
    """The reports root: the current campaign and the reports offered (PR 8c)."""
    from .admin_report_reads import read_report_list

    return read_report_list(context["caller"], runtime)


def report_participation(args, preamble, runtime, context):
    """The Participation page's statistics and daily table (PR 8c)."""
    from .admin_report_reads import read_participation

    return read_participation(context["caller"], runtime, scope=args.scope)


def report_comparison(args, preamble, runtime, context):
    """The Campaign comparison page's figures for a day (#1018)."""
    from .admin_report_reads import read_comparison

    return read_comparison(context["caller"], runtime, day=args.day)


def report_responses(args, preamble, runtime, context):
    """The response dashboard's funnel, counts only (PR 8c)."""
    from .admin_report_reads import read_responses

    return read_responses(context["caller"], runtime, mode=args.mode, grain=args.grain)


def report_financial(args, preamble, runtime, context):
    """The financial detail report's summary, without its rows (PR 8c)."""
    from .admin_report_reads import read_financial

    return read_financial(context["caller"], runtime)


def report_limitations(args, preamble, runtime, context):
    """The Limitations report's summary, without its rows (PR 8c, #970)."""
    from .admin_report_reads import read_limitations

    return read_limitations(context["caller"], runtime)


def report_information(args, preamble, runtime, context):
    """How many information queue items match the page's filters (PR 8c)."""
    from .admin_report_reads import read_information

    return read_information(
        context["caller"],
        runtime,
        {
            "disposition": args.disposition,
            "needed": args.needed,
            "completed": args.completed,
            "start": args.start,
            "end": args.end,
            "zone": args.zone,
        },
    )


def report_ministry(args, preamble, runtime, context):
    """The Ministry summary, or one Ministry's request count (PR 8c)."""
    from .admin_report_reads import read_ministry

    filters = {
        "history": args.history,
        "state": args.state,
        "start": args.start,
        "end": args.end,
        "zone": args.zone,
        "sort": args.sort,
        "page": None if args.page is None else str(args.page),
        "size": None if args.size is None else str(args.size),
    }
    return read_ministry(
        context["caller"],
        runtime,
        filters,
        ministry=args.ministry,
        requests=args.requests,
    )


def report_roster(args, preamble, runtime, context):
    """The Roster changes to enter list, as counts only (#463)."""
    from .admin_roster import read_roster

    return read_roster(
        context["caller"], runtime, show=args.show, ministry=args.ministry
    )


def _census_filters(args):
    """The page's non-identifying filters, as ``report census`` takes them."""
    return {
        "status": args.status,
        # The page's "Show history" box: "yes" when ticked.
        "history": "yes" if args.history else None,
        "route": args.route,
        "kind": args.kind,
        "start": args.start,
        "end": args.end,
        "zone": args.zone,
    }


def export_roster(args, preamble, runtime, context):
    """The Roster changes to enter download, streamed to standard output (#463)."""
    from .admin_roster_export import export_roster as export

    return export(
        context["caller"],
        runtime,
        show=args.show,
        ministry=args.ministry,
        fmt=args.format,
        zone_name=args.timezone,
        context=context,
    )


def export_limitations(args, preamble, runtime, context):
    """The Limitations download, streamed to standard output (#463)."""
    from .admin_limitations_export import export_limitations as export

    return export(
        context["caller"],
        runtime,
        show=args.show,
        fmt=args.format,
        zone_name=args.timezone,
        context=context,
    )


def export_census(args, preamble, runtime, context):
    """The Census changes download, streamed to standard output (#463)."""
    from .admin_census_export import export_census as export

    return export(
        context["caller"],
        runtime,
        _census_filters(args),
        fmt=args.format,
        zone_name=args.timezone,
        context=context,
    )


def report_census(args, preamble, runtime, context):
    """The Census changes list, as counts only (#463)."""
    from .admin_census import read_census

    return read_census(context["caller"], runtime, _census_filters(args))


def census_resolve(args, preamble, runtime, context):
    """Record a Census changes row's outcome, as its row actions do (#463).

    Without ``--request-key`` a new key is made and written to standard error
    before anything is done, so a run that crashes can be repeated with it.
    Entered in ParishSoft cannot be undone, so, as the page's confirmation
    dialog does, it asks first (or takes ``--yes``); Ignore and Reopen can
    be undone and ask nothing, as on the page.
    """
    from .accounts.policy import Capability
    from .admin_census import resolve_census
    from .responses.census_resolution import MAX_NOTE

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.VIEW_CENSUS)

    key = _request_key(args, context)
    # The page strips the note it posts; so does a note read from standard
    # input (``_input``).
    note = _input(args.note, context, MAX_NOTE * 4).strip()
    if args.action == "entered":
        confirm(
            context,
            (
                f"Mark census change {args.proposal_id} (version "
                f"{args.expected_version}) as entered in ParishSoft.",
                "Entered in ParishSoft cannot be undone.",
            ),
        )
    return resolve_census(
        context["caller"],
        runtime,
        args.proposal_id,
        action=args.action,
        expected_version=args.expected_version,
        note=note,
        request_key=key,
        context=context,
    )


def census_review(args, preamble, runtime, context):
    """Review an Automatic census change, as its row's actions do (#528).

    Approve (``approved``), Ignore (``ignored``) or Back to To review
    (``unreviewed``). Without ``--request-key`` a new key is made and
    written to standard error before anything is done. Every review can be
    changed again until publication of the change starts, so, as on the
    page, it asks nothing.
    """
    from .accounts.policy import Capability
    from .admin_census import review_census
    from .responses.census_resolution import MAX_NOTE

    # The role first (#1341): an Administrator's, so Staff are denied before
    # any input or lookup.
    _role_first(context, runtime, Capability.PUBLISH_CENSUS)

    key = _request_key(args, context)
    # The page strips the note it posts; so does a note read from standard
    # input (``_input``).
    note = _input(args.note, context, MAX_NOTE * 4).strip()
    return review_census(
        context["caller"],
        runtime,
        args.proposal_id,
        decision=args.decision,
        expected_version=args.expected_version,
        note=note,
        request_key=key,
        context=context,
    )


# The longest edited value ``census_review.edit_census_change`` reads, in
# characters; standard input is bounded by its UTF-8 bytes.
MAX_EDIT = 1000
TOO_LONG_EDIT = f"Enter text of at most {MAX_EDIT:,} characters."


def census_edit(args, preamble, runtime, context):
    """Edit the value publication writes, or clear the edit (#1434, ADM-09.04).

    ``--value`` (or ``-`` for standard input) is Edit value's Save value and
    ``--clear`` its Clear edit. Without ``--request-key`` a new key is made
    and written to standard error before anything is done. An edit keeps
    the decision and can be changed again until publication of the change
    starts, so, as on the page, it asks nothing.
    """
    from .accounts.policy import Capability
    from .admin_census import edit_census
    from .admin_changes import InvalidChange
    from .responses.census_resolution import MAX_NOTE

    # Standard input carries one input only.
    if args.value == "-" and args.note == "-":
        raise UsageError("Only one input may be read from standard input.")
    # The role first (#1341): an Administrator's, so Staff are denied before
    # any input or lookup.
    _role_first(context, runtime, Capability.PUBLISH_CENSUS)

    key = _request_key(args, context)
    note = _input(args.note, context, MAX_NOTE * 4).strip()
    # The service reads the value as the Family form reads its own answer,
    # which trims it; standard input is trimmed already (``_input``).
    value = None
    if not args.clear:
        try:
            value = _input(args.value, context, MAX_EDIT * 4)
        except ValueError:
            value = None
        # Longer than the service reads (or not UTF-8 text) is refused here,
        # with a fixed reason for the field, as the service's own reasons.
        if value is None or len(value) > MAX_EDIT:
            raise InvalidChange(
                [{"field": "value", "code": "invalid", "message": TOO_LONG_EDIT}]
            )
    return edit_census(
        context["caller"],
        runtime,
        args.proposal_id,
        value=value,
        expected_version=args.expected_version,
        note=note,
        request_key=key,
        context=context,
    )


def census_resolve_conflict(args, preamble, runtime, context):
    """Use the Family's value anyway on a census Conflict (#1434, ADM-09.03).

    Without ``--request-key`` a new key is made and written to standard
    error before anything is done. ``census review --decision
    ignored|unreviewed`` undoes it, so, as on the page, it asks nothing.
    """
    from .accounts.policy import Capability
    from .admin_census import resolve_conflict
    from .responses.census_resolution import MAX_NOTE

    # The role first (#1341), as census review.
    _role_first(context, runtime, Capability.PUBLISH_CENSUS)

    key = _request_key(args, context)
    note = _input(args.note, context, MAX_NOTE * 4).strip()
    return resolve_conflict(
        context["caller"],
        runtime,
        args.proposal_id,
        expected_version=args.expected_version,
        note=note,
        request_key=key,
        context=context,
    )


def report_source_form(args, preamble, runtime, context):
    """Families the form cannot open, by DUID only (#774)."""
    from .admin_source_form import read_source_form

    return read_source_form(context["caller"], runtime)


def report_data_to_fix(args, preamble, runtime, context):
    """Data to fix in ParishSoft's section counts, counts only (#1011)."""
    from .admin_data_to_fix import read_data_to_fix

    return read_data_to_fix(context["caller"], runtime)


def digest_list(args, preamble, runtime, context):
    """The Emailed reports page's lists of retained reports (ADM-11)."""
    from .admin_digests import read_list

    return read_list(context["caller"], runtime, requested=args.requested)


def digest_daily(args, preamble, runtime, context):
    """A retained daily report, as its page reads it (PR 8d)."""
    from .admin_digests import read_digest

    return read_digest(
        context["caller"], runtime, args.snapshot_id, kind="daily", page=None
    )


def digest_weekly(args, preamble, runtime, context):
    """A retained weekly report's items, as its page reads them (PR 8d)."""
    from .admin_digests import read_digest

    return read_digest(
        context["caller"], runtime, args.snapshot_id, kind="weekly", page=args.page
    )


def digest_weekly_request(args, preamble, runtime, context):
    """Request a manual weekly report, as its page's form does (PR 8d).

    The page asks the reader to tick that the report may repeat items
    already reported; the command asks the same at the prompt (or takes
    ``--yes``), before a request key is made or any transaction opens.
    """
    from .accounts.policy import Capability
    from .admin_digests import acknowledgement, request_weekly

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.BACKGROUND_WORK)

    confirm(context, ("Queue a new manual weekly report.", acknowledgement()))
    return request_weekly(
        context["caller"],
        runtime,
        request_key=_request_key(args, context),
        context=context,
    )


# The page's acknowledgements, asked at the prompt (PR 9c): the same words
# the person ticks on the delivery and refused address pages.
RESEND_ACKNOWLEDGEMENT = (
    "The prior attempt may already have been delivered. I accept the "
    "duplicate risk and explicitly authorize another attempt."
)
CLEAR_ACKNOWLEDGEMENT = (
    "I verified this address for this Family and authorize removing this refusal."
)
BOUNCE_ACKNOWLEDGEMENT = "I saw a bounce message for this address from this email."
# The page's only-address notice, without the address (#1211).
BOUNCE_LAST_ADDRESS = (
    "This is the Family's only address left: the Family will get no campaign "
    "email until it is cleared or ParishSoft is corrected."
)


def delivery_resend(args, preamble, runtime, context):
    """Resend one uncertain delivery, as its page's **Resend** does (PR 9c).

    The note is read and the request key made first, so a refused prompt
    has nothing left to read and the key is already on standard error; the
    prompt comes before any transaction opens.
    """
    from .accounts.policy import Capability
    from .admin_operations import NOTE_LIMIT, resend_delivery_command

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.BACKGROUND_WORK)

    key = _request_key(args, context)
    note = _input(args.note, context, NOTE_LIMIT * 4)
    confirm(
        context,
        (
            f"Resend delivery {args.message_id} (version {args.expected_version}).",
            RESEND_ACKNOWLEDGEMENT,
        ),
    )
    return resend_delivery_command(
        context["caller"],
        runtime,
        args.message_id,
        expected_version=args.expected_version,
        note=note,
        request_key=key,
        context=context,
    )


def delivery_refusal_clear(args, preamble, runtime, context):
    """Clear one verified refused address, as its page's form does (PR 9c)."""
    from .accounts.policy import Capability
    from .admin_operations import NOTE_LIMIT, clear_refusal_command

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.BACKGROUND_WORK)

    key = _request_key(args, context)
    note = _input(args.note, context, NOTE_LIMIT * 4)
    confirm(
        context,
        (f"Clear refused address {args.refusal_id}.", CLEAR_ACKNOWLEDGEMENT),
    )
    return clear_refusal_command(
        context["caller"],
        runtime,
        args.refusal_id,
        source_snapshot_id=args.source_snapshot_id,
        source_generation=args.source_generation,
        note=note,
        request_key=key,
        context=context,
    )


def delivery_bounce_report(args, preamble, runtime, context):
    """Report a bounced address, as the Mail message page's form does (#1211).

    The address is trimmed and lower-cased, the form the page offers and the
    service requires, so one copied from a bounce message is accepted. It
    asks for the page's tick at the prompt (or takes ``--yes``), as
    ``delivery refusal-clear`` does, before any transaction opens; the
    summary names the email, never the address or the note, and warns, as
    the page's notice does, when the address is the Family's only one left.
    """
    from .accounts.policy import Capability
    from .admin_operations import NOTE_LIMIT, report_bounce_command

    # The role first (#1341): denied before any input, lookup or prompt.
    _role_first(context, runtime, Capability.BACKGROUND_WORK)

    key = _request_key(args, context)
    note = _input(args.note, context, NOTE_LIMIT * 4)

    def ask(last):
        """The page's tick, with its only-address notice when it applies."""
        confirm(
            context,
            (
                f"Report that delivery {args.message_id} bounced at the given "
                "address, setting it aside for the Family until it is cleared.",
                *((BOUNCE_LAST_ADDRESS,) if last else ()),
                BOUNCE_ACKNOWLEDGEMENT,
            ),
        )

    return report_bounce_command(
        context["caller"],
        runtime,
        args.message_id,
        address=args.address.strip().lower(),
        note=note,
        request_key=key,
        context=context,
        ask=ask,
    )


def users_list(args, preamble, runtime, context):
    """The Portal users, sorted as the Users table (#952 slice 2)."""
    from .admin_reads import read_users

    return read_users(context["caller"], runtime, args.sort, audit=context["audit"])


def users_preview(args, preamble, runtime, context):
    """Review a change to Admin portal users (#952 slice 5)."""
    from .admin_users import CHANGES_LIMIT, preview_users

    return preview_users(
        context["caller"],
        runtime,
        expected_version=args.expected_version,
        changes=_input(args.changes, context, CHANGES_LIMIT),
    )


def users_confirm(args, preamble, runtime, context):
    """Confirm a reviewed user change as a configuration request (#952)."""
    from .admin_users import TOKEN_LIMIT, confirm_users

    return confirm_users(
        context["caller"],
        runtime,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def users_import_leaders(args, preamble, runtime, context):
    """Review or confirm importing ParishSoft Ministry leaders (#952 slice 6)."""
    from .admin_users import TOKEN_LIMIT, confirm_import, preview_import

    if args.confirm:
        if args.token is None or args.all or args.email:
            raise UsageError("--confirm takes only --token.")
        return confirm_import(
            context["caller"],
            runtime,
            token=_input(args.token, context, TOKEN_LIMIT),
            context=context,
        )
    if args.token is not None or (args.all and args.email):
        raise UsageError("--preview takes --all or --email, not both, and no --token.")
    return preview_import(
        context["caller"], runtime, everyone=args.all, emails=args.email or []
    )


def users_preview_upgrade(args, preamble, runtime, context):
    """The read-only users upgrade preview of Portal users (#952 slice 2)."""
    from .admin_reads import read_users_upgrade

    return read_users_upgrade(context["caller"], runtime, audit=context["audit"])


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
    """Options of ``schedule show``, ``go-live readiness`` and ``campaign shares``."""
    parser.add_argument(
        "--campaign", type=_uuid, help="the campaign (default: the current one)"
    )


def _digest(value):
    """A configuration digest (``schedule show``'s ``version``): 64 hex digits."""
    import re

    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise argparse.ArgumentTypeError("not a configuration version")
    return value


def _schedule_preview_options(parser):
    """Options of ``schedule preview``: the campaign, its version and the change."""
    _schedule_options(parser)
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version schedule show printed",
    )
    parser.add_argument(
        "--changes",
        required=True,
        help="the change document as JSON, or - to read it from standard input",
    )


def _schedule_confirm_options(parser):
    """Options of ``schedule confirm``: the campaign and the preview's token."""
    _schedule_options(parser)
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from schedule preview, or - for standard input",
    )


def _config_request_options(parser):
    """Options of ``config request show``: the request, and --watch."""
    parser.add_argument("request_id", type=_uuid, metavar="REQUEST_ID")
    _watch_options(parser)


def _config_cancel_options(parser):
    """Options of ``config request cancel``: the request and the reason."""
    parser.add_argument("request_id", type=_uuid, metavar="REQUEST_ID")
    parser.add_argument(
        "--reason",
        required=True,
        help="why it is cancelled, or - to read it from standard input (needs --yes)",
    )


def _task_retry_options(parser):
    """Options of ``task retry``: the failed task, and the request key."""
    parser.add_argument("task_id", type=_uuid, metavar="TASK_ID")
    _request_key_option(parser)


# Outgoing mail's state filter. Spelled out because the parser is built
# before Django is set up, when ``jobs.delivery_metadata`` cannot be
# imported; a test keeps it equal to ``delivery_metadata.STATES``.
DELIVERY_STATES = (
    "all",
    "delivery_unknown",
    "permanent_failure",
    "pending",
    "retry_wait",
    "submitting",
    "delivered",
    "cancelled",
)
# The resolutions ``delivery resolve`` offers (``admin_operations``); a test
# keeps them equal.
RESOLVE_ACTIONS = (
    "note",
    "accept",
    "confirm_unsent",
    "retry_failed",
    "retry_unsent",
)


def _request_key_option(parser):
    """``--request-key``: a UUID that makes a repeat safe."""
    parser.add_argument(
        "--request-key",
        type=_uuid,
        help="a UUID that makes a repeat safe (default: a new one, "
        "written to standard error)",
    )


def _uuid4(value):
    """A canonical version 4 UUID, as the pages' request keys must be."""
    parsed = _uuid(value)
    if parsed.version != 4:
        raise argparse.ArgumentTypeError("not a version 4 UUID")
    return parsed


def _backup_now_options(parser):
    """Options of ``system backup-now``: the request key, a version 4 UUID,
    which becomes the request's identity, as the page's preview key does."""
    parser.add_argument(
        "--request-key",
        type=_uuid4,
        help="a UUID that makes a repeat safe (default: a new one, "
        "written to standard error)",
    )


def _debug_off_options(parser):
    """Options of ``system debug-off``: the request key, a version 4 UUID,
    which becomes the switch's identity, as the page's preview key does."""
    parser.add_argument(
        "--request-key",
        type=_uuid4,
        help="a UUID that makes a repeat safe (default: a new one, "
        "written to standard error)",
    )


def _refresh_accept_options(parser):
    """Options of ``system refresh-accept``: the preview's token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from system refresh-accept-preview, or - for "
        "standard input (then answer the prompt with --yes)",
    )


def _debug_allow_options(parser):
    """Options of ``system debug-allow``: the switch to clear, which makes a
    repeat safe (default: the switch set now)."""
    parser.add_argument(
        "--switch",
        type=_uuid,
        help="the debug-off switch to clear, as an earlier run printed it "
        "(default: the switch set now)",
    )


def _refresh_start_options(parser):
    """Options of ``refresh start``: the request key, a version 4 UUID."""
    parser.add_argument(
        "--request-key",
        type=_uuid4,
        help="a UUID that makes a repeat safe (default: a new one, "
        "written to standard error)",
    )


def _sample_preview_options(parser):
    """Options of ``test sample-preview``: the email revision and the key."""
    parser.add_argument("revision_id", type=_uuid, metavar="REVISION_ID")
    parser.add_argument(
        "--request-key",
        type=_uuid4,
        help="a version 4 UUID the token binds, so a send is safe to repeat "
        "(default: a new one)",
    )


def _duid(value):
    """A Family DUID option value, as the page accepts one."""
    from .jobs.delivery_metadata import family_duid

    try:
        return family_duid(value)
    except ValueError:
        raise argparse.ArgumentTypeError("not a Family DUID") from None


def _families_preview_options(parser):
    """Options of ``test families-preview``: the email, the Families, the key
    and, for the names export (#817), ``--names`` with its optional time zone
    and file format (#966)."""
    parser.add_argument("revision_id", type=_uuid, metavar="REVISION_ID")
    parser.add_argument(
        "--family",
        required=True,
        action="append",
        type=_duid,
        metavar="DUID",
        help="a ParishSoft Family DUID; repeat for up to ten Families",
    )
    parser.add_argument(
        "--request-key",
        type=_uuid4,
        help="a version 4 UUID the token binds, so a send is safe to repeat "
        "(default: a new one)",
    )
    parser.add_argument(
        "--names",
        action="store_true",
        help="also request an export of the Families' names, which the "
        "page shows, for export fetch",
    )
    parser.add_argument(
        "--timezone",
        help="with --names: the time zone of the file's capture and request "
        "times, for example America/New_York (default: the parish's)",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "xlsx", "pdf"),
        help=f"with --names: the file format (default {DEFAULT_EXPORT_FORMAT})",
    )


def _sample_test_options(parser):
    """Options of ``test sample``: the reviewed token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from test sample-preview, or - to read it from "
        "standard input (needs --yes)",
    )


def _portal_maintenance_set_options(parser):
    """Options of ``portal maintenance set``: the state and the optional note."""
    parser.add_argument("--state", required=True, choices=("closed", "open"))
    parser.add_argument(
        "--message",
        help="with --state closed: the note Families see (at most 500 "
        "characters, no email address), or - to read it from standard input "
        "(needs --yes)",
    )


# --- Options of Pause and resume mail's controls (PR 7b, #463) ----------------


def _send_reason_options(parser):
    """The page's reason, which every pause, resume and resolution asks for."""
    parser.add_argument(
        "--reason",
        required=True,
        help="why (at most 1,024 characters; the page shows it), or - to read "
        "it from standard input",
    )


def _send_resolve_options(parser):
    """Options of ``send resolve-preview``: the decision and message types."""
    from .admin_delivery_control import RESOLVE_DECISIONS, RESOLVE_TYPES

    parser.add_argument("--decision", required=True, choices=RESOLVE_DECISIONS)
    parser.add_argument(
        "--type",
        action="append",
        choices=RESOLVE_TYPES,
        help="a held message type to release or cancel (repeatable); none "
        "with --decision clear",
    )


def _send_confirm_options(parser):
    """Options of ``send confirm``: the reviewed preview's token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from send pause-preview, resume-preview or "
        "resolve-preview, or - for standard input (needs --yes)",
    )


# --- end of the options of Pause and resume mail's controls -------------------


def _history_year(value):
    """A campaign archive's pledge year: four digits, 2000 to 2100."""
    if not (len(value) == 4 and value.isdecimal() and 2000 <= int(value) <= 2100):
        raise argparse.ArgumentTypeError("not a year")
    return int(value)


def _history_preview_options(parser):
    """Options of ``history preview``: ``-`` for standard input, the box."""
    parser.add_argument(
        "file",
        choices=("-",),
        metavar="-",
        help="read the workbook's bytes from standard input",
    )
    parser.add_argument(
        "--no-family-rows",
        action="store_true",
        help="do not keep each Family's id and first day (the renewal figure)",
    )


def _history_confirm_options(parser):
    """Options of ``history confirm``: the preview's token."""
    parser.add_argument(
        "--token", type=_uuid, required=True, help="preview.token from history preview"
    )


def _history_remove_options(parser):
    """Options of ``history remove``: the year and its version."""
    parser.add_argument("year", type=_history_year, metavar="YEAR")
    parser.add_argument(
        "--expected-version",
        type=_version,
        required=True,
        help="the import's version from history list",
    )


def _content_show_options(parser):
    """Options of ``content show``: the page or email, as ``content list``
    names it, and the editor's "start from the default text"."""
    parser.add_argument("kind", choices=("page", "email"), metavar="KIND")
    parser.add_argument("slot", metavar="SLOT", help="a content list slot")
    parser.add_argument(
        "--revision",
        type=_uuid,
        help="one email of the mail type, by its content list id",
    )
    parser.add_argument(
        "--default",
        action="store_true",
        help="print the slot's default text instead; nothing changes",
    )


# The integration targets, spelled out because the parser is built before
# Django is set up; a test keeps them equal to ``admin_integrations``'.
INTEGRATION_SHOW_TARGETS = (
    "parishsoft",
    "google_workspace",
    "email",
    "slack",
    "backup",
)
INTEGRATION_STATUS_TARGETS = ("parishsoft", "google_workspace", "slack", "backup")
# The integrations with a key change line to dismiss (the pages' rotating keys).
INTEGRATION_DISMISS_TARGETS = ("parishsoft", "google_workspace", "slack")


def _integration_show_options(parser):
    """Options of ``integration show``: the integration, as ``integration list``."""
    parser.add_argument("target", choices=INTEGRATION_SHOW_TARGETS, metavar="TARGET")


def _integration_status_options(parser):
    """Options of ``integration status``: the integration, and --watch."""
    parser.add_argument("target", choices=INTEGRATION_STATUS_TARGETS, metavar="TARGET")
    _watch_options(parser)


def _integration_preview_options(parser):
    """Options of ``integration preview``: the integration, version and change."""
    parser.add_argument("target", choices=INTEGRATION_SHOW_TARGETS, metavar="TARGET")
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version integration show printed",
    )
    change = parser.add_mutually_exclusive_group()
    change.add_argument(
        "--changes",
        help="the settings change as JSON, or - to read it from standard input",
    )
    change.add_argument(
        "--remove",
        action="store_true",
        help="review removing the integration (Slack or off-site backups)",
    )
    parser.add_argument(
        "--refresh-schedule",
        help="ParishSoft's refresh schedule as YAML or JSON in the stored "
        "refresh_rules shape, or - to read it from standard input",
    )


def _integration_schedule_preview_options(parser):
    """Options of ``integration schedule-preview``: a proposed schedule, if any."""
    parser.add_argument(
        "--refresh-schedule",
        help="a proposed refresh schedule as YAML or JSON in the stored "
        "refresh_rules shape, or - to read it from standard input "
        "(default: the applied schedule)",
    )


def _integration_confirm_options(parser):
    """Options of ``integration confirm``: the integration and the token."""
    parser.add_argument("target", choices=INTEGRATION_SHOW_TARGETS, metavar="TARGET")
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from integration preview, or - for standard input "
        "(needs --yes)",
    )


def _integration_backup_probe_options(parser):
    """Options of ``integration backup-probe``: a folder link, or the saved one."""
    parser.add_argument(
        "--folder",
        metavar="LINK",
        help="the Google Drive folder link to check (default: the saved folder)",
    )


def _integration_dismiss_options(parser):
    """Options of ``integration dismiss``: the integration and its key change."""
    parser.add_argument("target", choices=INTEGRATION_DISMISS_TARGETS, metavar="TARGET")
    parser.add_argument(
        "--request-id",
        required=True,
        type=_uuid,
        help="key.request_id from integration show or integration status",
    )


# The integrations whose key the settings pages replace; a test keeps them
# equal to ``admin_integration_keys.KEY_TARGETS``.
INTEGRATION_KEY_TARGETS = ("parishsoft", "google_workspace", "slack")


def _integration_key_status_options(parser):
    """Options of ``integration key status``: the integration, a key change, --watch."""
    parser.add_argument("target", choices=INTEGRATION_KEY_TARGETS, metavar="TARGET")
    parser.add_argument(
        "--request-id",
        type=_uuid,
        help="one of your key changes (default: your latest for TARGET)",
    )
    _watch_options(parser)


def _integration_key_target_options(parser):
    """Options of ``integration key finish-switching-preview``: the integration."""
    parser.add_argument("target", choices=INTEGRATION_KEY_TARGETS, metavar="TARGET")


def _integration_key_finish_options(parser):
    """Options of ``integration key finish-switching``: the integration and token."""
    parser.add_argument("target", choices=INTEGRATION_KEY_TARGETS, metavar="TARGET")
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from integration key finish-switching-preview, or - "
        "for standard input (needs --yes)",
    )


def _content_preview_options(parser):
    """Options of ``content preview``: the emails to delete, or HTML to preview."""
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--delete",
        action="append",
        type=_uuid,
        metavar="ID",
        help="an email to delete, by its content list revision id (repeatable)",
    )
    mode.add_argument(
        "--html",
        help="HTML to preview as the editor's plain text, or - for standard input",
    )
    parser.add_argument(
        "--expected-version",
        type=_digest,
        help="the version content list printed (with --delete)",
    )


def _content_confirm_options(parser):
    """Options of ``content confirm``: the preview's token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from content preview, or - for standard input",
    )


def _content_history_options(parser):
    """Options of ``content history``: one retained revision's sample."""
    parser.add_argument(
        "--revision",
        type=_uuid,
        help="a retained page or email, by its entries id: print its sample",
    )


def _events_acknowledge_options(parser):
    """Options of ``events acknowledge``: one security event, or the banner."""
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--security",
        type=_uuid,
        metavar="EVENT_ID",
        help="a security event's id from events list",
    )
    target.add_argument(
        "--critical",
        metavar="TOKEN",
        help="critical.token from events list, or - for standard input",
    )


# Campaign images' slots, in the page's order. Spelled out because the parser
# is built before Django is set up; a test keeps it equal to
# ``campaigns.configuration.ARTWORK_SLOTS``.
ARTWORK_SLOTS = ("banner", "welcome", "member", "financial", "closing")


def _artwork_remove_preview_options(parser):
    """Options of ``artwork remove-preview``: the slot and the version."""
    parser.add_argument("slot", choices=ARTWORK_SLOTS, metavar="SLOT")
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version artwork show printed",
    )


def _artwork_remove_options(parser):
    """Options of ``artwork remove``: the slot and the preview's token."""
    parser.add_argument("slot", choices=ARTWORK_SLOTS, metavar="SLOT")
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from artwork remove-preview, or - for standard input",
    )


def _files_delete_options(parser):
    """Options of ``files delete``: the files, by ``files list`` id."""
    parser.add_argument("file_id", type=_uuid, nargs="+", metavar="FILE_ID")


def _files_rename_options(parser):
    """Options of ``files rename``: the file and its new placeholder name."""
    parser.add_argument("file_id", type=_uuid, metavar="FILE_ID")
    parser.add_argument(
        "--slug",
        required=True,
        help="the new placeholder name: lowercase letters, digits and single "
        "hyphens, up to 64 characters",
    )


def _delivery_list_options(parser):
    """Options of ``delivery list``: Outgoing mail's filters."""
    parser.add_argument("--state", choices=DELIVERY_STATES, help="default all")
    parser.add_argument(
        "--send", help="one Family email send (send history's send value)"
    )
    parser.add_argument(
        "--search", help="an exact Family DUID or delivery ID, as the page's search"
    )
    _page_options(parser)


def _delivery_show_options(parser):
    """Options of ``delivery show``: the delivery and its history window."""
    parser.add_argument("message_id", type=_uuid, metavar="MESSAGE_ID")
    _page_options(parser, sort=False)


def _refusal_list_options(parser):
    """Options of ``delivery refusals``: one Family's DUID, and the window."""
    parser.add_argument("--duid", help="an exact Family DUID")
    _page_options(parser)


def _refusal_show_options(parser):
    """Options of ``delivery refusal-show``: the refusal."""
    parser.add_argument("refusal_id", type=_uuid, metavar="REFUSAL_ID")


def _version(value):
    """A record version (``delivery show``'s ``version``): a positive integer."""
    if not value.isdecimal() or not 1 <= int(value) <= 2**63 - 1:
        raise argparse.ArgumentTypeError("not a version")
    return int(value)


def _delivery_resolve_options(parser):
    """Options of ``delivery resolve``: the page's resolution form."""
    parser.add_argument("message_id", type=_uuid, metavar="MESSAGE_ID")
    parser.add_argument("--action", required=True, choices=RESOLVE_ACTIONS)
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_version,
        help="the delivery's version from delivery show",
    )
    parser.add_argument(
        "--note",
        required=True,
        help="the evidence note, or - to read it from standard input",
    )
    _request_key_option(parser)


# The System logs page's six kinds of entry (``audit.log_rows``): the five
# levels and audit records. Spelled out because the parser is built before
# Django is set up; a test keeps it equal to the page's.
LOG_KINDS = ("debug", "info", "warning", "error", "critical", "audit")
# The page's Activity groups (#953, ``audit.log_rows.ACTIVITY_TYPES``),
# spelled out for the same reason; a test keeps them equal to the page's.
LOG_ACTIVITIES = (
    "configuration",
    "downloads",
    "family_mail",
    "family_portal",
    "parishsoft",
    "security",
    "sign_in",
)


def _log_filter_options(parser):
    """The System logs page's filters, shared by ``logs list`` and ``logs export``."""
    parser.add_argument(
        "--show",
        action="append",
        choices=LOG_KINDS,
        help="a kind of entry to show; repeat for more (default: every "
        "level but debug, and audit records)",
    )
    parser.add_argument(
        "--activity",
        choices=LOG_ACTIVITIES,
        help="a fixed group of types listed together, as the page's Activity "
        "choice: family_mail (outgoing email steps, resolutions, pauses, tests "
        "and provider failures) or parishsoft (refreshes, quick updates, "
        "failures and holds), with operational entries; or, audit records "
        "only, sign_in (every Admin portal sign-in event), configuration "
        "(settings, schedule, user, file and key changes), downloads (report "
        "and export requests and downloads), security (widened access, "
        "automation sessions, access recovery and encryption keys) or "
        "family_portal (Family sign-ins, responses, links and maintenance)",
    )
    parser.add_argument("--event", metavar="TYPE", help="one event type, exactly")
    parser.add_argument("--actor", type=_uuid, help="the acting user or process")
    parser.add_argument("--correlation", type=_uuid, help="one correlation id")
    parser.add_argument(
        "--campaign", type=_uuid, help="one campaign (audit records only)"
    )
    parser.add_argument(
        "--subject", type=_uuid, help="one subject record (audit records only)"
    )
    parser.add_argument(
        "--text",
        help="search text, as the page's search (no email address); give it "
        "only when needed, since a shell keeps it in its history",
    )
    parser.add_argument("--ministry", metavar="DUID", help="one Ministry's DUID")
    parser.add_argument("--start", metavar="DATE", help="from this day (YYYY-MM-DD)")
    parser.add_argument("--end", metavar="DATE", help="through this day (YYYY-MM-DD)")
    parser.add_argument(
        "--zone",
        help="the time zone --start and --end days fall in (required with them), "
        "for example America/New_York",
    )


def _logs_list_options(parser):
    """Options of ``logs list``: the page's filters, snapshot and window."""
    _log_filter_options(parser)
    parser.add_argument(
        "--through",
        metavar="INSTANT",
        help="the snapshot a previous logs list printed, to page through it",
    )
    parser.add_argument("--page", type=int, help="page number (default 1)")
    parser.add_argument("--size", type=int, help="rows per page: 25, 50, 100 or 250")
    parser.add_argument(
        "--sort", choices=("newest", "oldest"), help="default newest first"
    )


def _logs_export_options(parser):
    """Options of ``logs export``: the page's filters, format and time zone."""
    _log_filter_options(parser)
    parser.add_argument(
        "--format",
        choices=("csv", "xlsx", "jsonl"),
        default=DEFAULT_EXPORT_FORMAT,
        help=f"default {DEFAULT_EXPORT_FORMAT}",
    )
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="the time zone of the file's times (default UTC)",
    )


# The file format a command takes when --format is left out (#1190):
# ``web.exports.DEFAULT_EXPORT_FORMAT``, the pages' preselected format.
# Spelled out because the parser is built before Django is set up; a test
# keeps the two equal.
DEFAULT_EXPORT_FORMAT = "xlsx"

# The participation export's formats (``export_services.create_export``).
# Spelled out because the parser is built before Django is set up; a test
# keeps it equal to the service's.
EXPORT_FORMATS = ("csv", "png", "pdf", "xlsx")


def _export_create_options(parser):
    """Options of ``export create``: the generation, format, zone and key."""
    parser.add_argument(
        "--fact-set",
        required=True,
        type=_uuid,
        help="the participation generation to export (its fact set id)",
    )
    parser.add_argument("--format", required=True, choices=EXPORT_FORMATS)
    parser.add_argument(
        "--timezone",
        required=True,
        help="the time zone of the file's dates, for example America/New_York",
    )
    _request_key_option(parser)


# The Family-level exports' formats (the export services' own).
FAMILY_EXPORT_FORMATS = ("csv", "xlsx", "pdf")


def _export_comparison_options(parser):
    """Options of ``export comparison``: the day, format, optional zone and key."""
    parser.add_argument(
        "--day",
        type=int,
        help="the campaign day to compare on (default: the last complete day)",
    )
    parser.add_argument("--format", required=True, choices=FAMILY_EXPORT_FORMATS)
    parser.add_argument(
        "--timezone",
        help="the time zone of the file's request time, for example "
        "America/New_York (default: the parish's time zone)",
    )
    _request_key_option(parser)


def _family_export_options(parser, *, filters=True):
    """The format, time zone and key every Family-level export takes."""
    parser.add_argument("--format", required=True, choices=FAMILY_EXPORT_FORMATS)
    parser.add_argument(
        "--timezone",
        required=True,
        help="the time zone of the file's times, for example America/New_York",
    )
    if filters:
        parser.add_argument(
            "--filter",
            action="append",
            metavar="NAME=VALUE",
            help="one of the page's filters, as its form names it; repeat for "
            "more (no search or Family code: it would name a Family)",
        )
    _request_key_option(parser)


def _export_information_options(parser):
    """Options of ``export information``: filters and workflow history."""
    _family_export_options(parser)
    parser.add_argument(
        "--history", action="store_true", help="include each item's workflow history"
    )


def _export_ministry_options(parser):
    """Options of ``export ministry``: one Ministry's join or leave list."""
    _family_export_options(parser)
    # Both are needed; a missing one is the page's "invalid" refusal, not a
    # usage error, as for every other bad selection.
    parser.add_argument("--ministry", type=_ministry_duid, help="the Ministry's DUID")
    parser.add_argument(
        "--requests",
        choices=("join", "leave"),
        help="export its join or leave requests",
    )


def _export_ministry_packet_options(parser):
    """Options of ``export ministry-packet``: the Ministries and history."""
    _family_export_options(parser, filters=False)
    parser.add_argument(
        "--ministry",
        type=_ministry_duid,
        action="append",
        help="a Ministry to include, by its DUID; repeat for more (default: "
        "every Ministry you may report on)",
    )
    parser.add_argument(
        "--history", action="store_true", help="include earlier request versions"
    )


def _exact_create_options(parser):
    """Options of ``export exact create``: population, format, zone and key."""
    parser.add_argument(
        "--scope",
        choices=("historical", "current"),
        default="historical",
        help="historical (default: each day's population then) or current",
    )
    parser.add_argument("--format", required=True, choices=EXPORT_FORMATS)
    parser.add_argument(
        "--timezone",
        required=True,
        help="the time zone of the file's dates, for example America/New_York",
    )
    _request_key_option(parser)


def _exact_id_option(parser):
    """The exact daily export a command acts on."""
    parser.add_argument("exact_id", type=_uuid, metavar="EXACT_ID")


def _exact_status_options(parser):
    """Options of ``export exact status``: the request, and --watch."""
    _exact_id_option(parser)
    _watch_options(parser)


def _exact_retry_options(parser):
    """Options of ``export exact retry``: the request and the key."""
    _exact_id_option(parser)
    _request_key_option(parser)


def _export_family_timeline_options(parser):
    """Options of ``export family-timeline``: the Family, mode, format and zone."""
    parser.add_argument(
        "family_id",
        type=_uuid,
        metavar="FAMILY_RECORD_ID",
        help="the Family's record id, as in its timeline page's address",
    )
    parser.add_argument(
        "--mode",
        choices=("production", "testing"),
        default="production",
        help="production (default) or the Testing rehearsal",
    )
    _family_export_options(parser, filters=False)


def _export_id_option(parser):
    """The export a lifecycle command acts on."""
    parser.add_argument("export_id", type=_uuid, metavar="EXPORT_ID")


def _export_status_options(parser):
    """Options of ``export status``: the export, and --watch."""
    _export_id_option(parser)
    _watch_options(parser)


def _export_keyed_options(parser):
    """Options of ``export retry`` and ``export regenerate``: export and key."""
    _export_id_option(parser)
    _request_key_option(parser)


def _export_download_options(parser):
    """Options of ``export download``: the export, and the required --stream."""
    _export_id_option(parser)
    parser.add_argument(
        "--stream",
        action="store_true",
        required=True,
        help="write the file's bytes to standard output (redirect it to a file)",
    )


def _report_participation_options(parser):
    """Options of ``report participation``: the page's scope."""
    parser.add_argument(
        "--scope",
        help="historical (default: each day's population as of that day) or "
        "current (today's population)",
    )


def _report_comparison_options(parser):
    """Options of ``report comparison``: the campaign day to compare on."""
    parser.add_argument(
        "--day",
        type=int,
        help="the campaign day to compare on (default: the last complete day)",
    )


def _report_responses_options(parser):
    """Options of ``report responses``: the dashboard's mode and grain."""
    parser.add_argument(
        "--mode", help="production (default) or testing (Administrators only)"
    )
    parser.add_argument(
        "--grain", help="the activity buckets: auto (default), hour or day"
    )


def _date_options(parser):
    """--start, --end and --zone: submitted on or after, and on or before, a
    day in a time zone, as the pages take them in the browser's zone (#558)."""
    parser.add_argument(
        "--start", metavar="DATE", help="submitted on or after this day (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end", metavar="DATE", help="submitted on or before this day (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--zone",
        help="the time zone --start and --end days fall in (required with them), "
        "for example America/New_York",
    )


def _report_information_options(parser):
    """Options of ``report information``: the queue's non-identifying filters."""
    parser.add_argument(
        "--disposition",
        help="current_actionable (default), superseded, withdrawn or all",
    )
    parser.add_argument("--needed", help="follow-up needed: any (default), yes or no")
    parser.add_argument("--completed", help="followed up: any (default), yes or no")
    _date_options(parser)


def _ministry_duid(value):
    """A Ministry DUID option value: a canonical integer.

    Its range (1 through 2**31 - 1) is the page's check, so a value outside
    it is the page's refusal (``invalid``), not a usage error.
    """
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value:
        raise argparse.ArgumentTypeError("not a Ministry DUID")
    return int(value)


def _report_ministry_options(parser):
    """Options of ``report ministry``: the summary, or one Ministry's list."""
    parser.add_argument(
        "--sort",
        help="the summary's order: name (default), name_desc, duid or duid_desc "
        "(by Ministry DUID)",
    )
    parser.add_argument("--page", type=int, help="summary page number (default 1)")
    parser.add_argument("--size", type=int, help="rows per page: 25, 50, 100 or 250")
    parser.add_argument(
        "--ministry", type=_ministry_duid, help="one Ministry, by its DUID"
    )
    parser.add_argument(
        "--requests",
        choices=("join", "leave"),
        help="with --ministry: count its join or leave requests",
    )
    parser.add_argument(
        "--state", help="with --ministry: a request state (default any)"
    )
    parser.add_argument(
        "--history",
        help="with --ministry: current (default) or all request versions",
    )
    _date_options(parser)


def _report_roster_options(parser):
    """Options of ``report roster``: the page's Show and Ministry choices."""
    parser.add_argument(
        "--show",
        choices=("todo", "all"),
        default="todo",
        help="todo: not yet entered in ParishSoft (default); all: entered too",
    )
    parser.add_argument(
        "--ministry", type=_ministry_duid, help="one Ministry, by its DUID"
    )


def _export_roster_options(parser):
    """Options of ``export roster``: ``report roster``'s, format and time zone.

    The format choices are the page's own (``ministry_roster_views.
    FORMATS``, which a test keeps equal), listed here because the parser is
    built before Django is set up.
    """
    _report_roster_options(parser)
    parser.add_argument(
        "--format",
        choices=("csv", "xlsx", "pdf"),
        default=DEFAULT_EXPORT_FORMAT,
        help=f"default {DEFAULT_EXPORT_FORMAT}",
    )
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="the time zone of the file's times (default UTC)",
    )


def _export_limitations_options(parser):
    """Options of ``export limitations``: the page's Show, format and time zone.

    The page's search is left out: no command takes text that names a
    Family. The format choices are the page's own (``limitation_views.
    FORMATS``, which a test keeps equal), listed here because the parser is
    built before Django is set up.
    """
    parser.add_argument(
        "--show",
        choices=("any", "cannot_serve", "cannot_attend"),
        default="any",
        help="any (default), cannot_serve (cannot participate in ministries) "
        "or cannot_attend (cannot attend Mass or prayer services)",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "xlsx", "pdf"),
        default=DEFAULT_EXPORT_FORMAT,
        help=f"default {DEFAULT_EXPORT_FORMAT}",
    )
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="the time zone of the file's times (default UTC)",
    )


def _export_census_options(parser):
    """Options of ``export census``: the page's filters, format and time zone."""
    _report_census_options(
        parser, history="include superseded and cancelled changes too"
    )
    parser.add_argument(
        "--format",
        choices=("csv", "xlsx", "pdf", "jsonl"),
        default=DEFAULT_EXPORT_FORMAT,
        help=f"default {DEFAULT_EXPORT_FORMAT}; jsonl adds each change's id, "
        "version and actions",
    )
    parser.add_argument(
        "--timezone",
        default="UTC",
        help="the time zone of the file's times (default UTC)",
    )


def _report_census_options(
    parser, *, history="count superseded and cancelled changes too"
):
    """Options of ``report census``: the page's filters, without its search.

    ``history`` is the ``--history`` help, which ``export census`` words for
    its rows.
    """
    parser.add_argument(
        "--status",
        help="open (default: the work to do), to_review, to_do, conflict, "
        "being_published, published, already, entered, ignored or all",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help=history,
    )
    parser.add_argument("--route", help="any (default), automatic or by_hand")
    parser.add_argument(
        "--kind", help="any (default), contact, moved, deceased or new_member"
    )
    _date_options(parser)


# A census change's outcomes (``responses.models.RESOLUTION_ACTIONS``),
# spelled out because the parser is built before Django is set up; a test
# keeps them equal.
CENSUS_ACTIONS = ("entered", "ignored", "reopened")


def _census_resolve_options(parser):
    """Options of ``census resolve``: a Census changes row's outcome."""
    parser.add_argument("proposal_id", type=_uuid, metavar="PROPOSAL_ID")
    parser.add_argument(
        "--action",
        required=True,
        choices=CENSUS_ACTIONS,
        help="entered (Entered in ParishSoft), ignored (Ignore) or reopened "
        "(Reopen, Administrators only)",
    )
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_version,
        help="the change's version, as its row on the page holds it",
    )
    parser.add_argument(
        "--note",
        default="",
        help="an optional note (required to reopen), or - to read it from "
        "standard input (needs --yes)",
    )
    _request_key_option(parser)


# An Administrator's review decisions (``responses.models.REVIEW_DECISIONS``),
# spelled out because the parser is built before Django is set up; a test
# keeps them equal.
CENSUS_DECISIONS = ("approved", "ignored", "unreviewed")


def _census_review_options(parser):
    """Options of ``census review``: an Automatic row's Review."""
    parser.add_argument("proposal_id", type=_uuid, metavar="PROPOSAL_ID")
    parser.add_argument(
        "--decision",
        required=True,
        choices=CENSUS_DECISIONS,
        help="approved (Approve), ignored (Ignore) or unreviewed (Back to To review)",
    )
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_version,
        help="the change's version, as export census --format jsonl prints it",
    )
    parser.add_argument(
        "--note",
        default="",
        help="an optional note, or - to read it from standard input",
    )
    _request_key_option(parser)


def _census_change_options(parser):
    """A census change, its version and an optional note, as review takes."""
    parser.add_argument("proposal_id", type=_uuid, metavar="PROPOSAL_ID")
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_version,
        help="the change's version, as export census --format jsonl prints it",
    )
    parser.add_argument(
        "--note",
        default="",
        help="an optional note, or - to read it from standard input",
    )
    _request_key_option(parser)


def _census_edit_options(parser):
    """Options of ``census edit``: Edit value's Save value or Clear edit."""
    _census_change_options(parser)
    edit = parser.add_mutually_exclusive_group(required=True)
    edit.add_argument(
        "--value",
        help="the value to publish instead of the Family's answer, or - to "
        "read it from standard input",
    )
    edit.add_argument(
        "--clear",
        action="store_true",
        help="clear the edit, so publication writes the Family's answer",
    )


def _snapshot_option(parser):
    """The retained report, by the id in its page's address."""
    parser.add_argument("snapshot_id", type=_uuid, metavar="SNAPSHOT_ID")


def _digest_list_options(parser):
    """Options of ``digest list``: the page's ``?requested=`` request key."""
    parser.add_argument(
        "--requested",
        type=_uuid,
        metavar="REQUEST_KEY",
        help="a digest weekly-request key: its task and, once ready, its report",
    )


def _digest_weekly_options(parser):
    """Options of ``digest weekly``: the retained report and a page of items."""
    _snapshot_option(parser)
    parser.add_argument("--page", help="the page of items (default 1)")


def _digest_weekly_request_options(parser):
    """Options of ``digest weekly-request``: the request key (``--yes`` is
    added for every prompting command)."""
    _request_key_option(parser)


def _delivery_resend_options(parser):
    """Options of ``delivery resend``: the page's Resend form."""
    parser.add_argument("message_id", type=_uuid, metavar="MESSAGE_ID")
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_version,
        help="the delivery's version from delivery show",
    )
    parser.add_argument(
        "--note",
        required=True,
        help="the evidence note, or - to read it from standard input (needs --yes)",
    )
    _request_key_option(parser)


def _refusal_clear_options(parser):
    """Options of ``delivery refusal-clear``: the page's clearance form."""
    parser.add_argument("refusal_id", type=_uuid, metavar="REFUSAL_ID")
    parser.add_argument(
        "--source-snapshot-id",
        required=True,
        type=_uuid,
        help="source snapshot_id from delivery refusal-show",
    )
    parser.add_argument(
        "--source-generation",
        required=True,
        type=_version,
        help="source generation from delivery refusal-show",
    )
    parser.add_argument(
        "--note",
        required=True,
        help="how you verified the address, or - to read it from standard "
        "input (needs --yes)",
    )
    _request_key_option(parser)


def _bounce_report_options(parser):
    """Options of ``delivery bounce-report``: the Mail message page's form."""
    parser.add_argument("message_id", type=_uuid, metavar="MESSAGE_ID")
    parser.add_argument(
        "--address",
        required=True,
        help="the address the bounce message names (one the email was sent to)",
    )
    parser.add_argument(
        "--note",
        required=True,
        help="what the bounce message said, or - to read it from standard "
        "input (needs --yes)",
    )
    _request_key_option(parser)


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


def _sessions_options(parser):
    """Options of ``sessions``: Automation access's filter and sort (#621)."""
    parser.add_argument(
        "--include-ended",
        action="store_true",
        help="also list sessions that ended in the last 30 days",
    )
    parser.add_argument(
        "--sort",
        help="label, scope, created, expires, used or ended; "
        "--sort=-NAME (with =) reverses it (default -created)",
    )


def _users_preview_options(parser):
    """Options of ``users preview``: the users' version and the change."""
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version users list printed",
    )
    parser.add_argument(
        "--changes",
        required=True,
        help="the change document as JSON, or - to read it from standard input",
    )


def _parish_preview_options(parser):
    """Options of ``parish preview``: the profile's version and the change."""
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version parish show printed",
    )
    parser.add_argument(
        "--changes",
        required=True,
        help="the change document as JSON, or - to read it from standard input",
    )


def _parish_confirm_options(parser):
    """Options of ``parish confirm``: the preview's token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from parish preview, or - for standard input",
    )


def _workgroup_preview_options(parser):
    """Options of ``workgroup preview``: the new name or --clear, and the version."""
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--name", help="the ParishSoft Family WorkGroup's name, as ParishSoft shows it"
    )
    choice.add_argument(
        "--clear",
        action="store_true",
        help="name no WorkGroup: every eligible Family gets the Reminders",
    )
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version workgroup show printed",
    )


def _workgroup_confirm_options(parser):
    """Options of ``workgroup confirm``: the preview's token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from workgroup preview, or - for standard input",
    )


def _campaign_preview_options(parser):
    """Options of ``campaign preview``: the settings' version and the change."""
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version campaign show printed",
    )
    parser.add_argument(
        "--changes",
        required=True,
        help="the change document as JSON, or - to read it from standard input",
    )


def _campaign_confirm_options(parser):
    """Options of ``campaign confirm``: the preview's token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from campaign preview, or - for standard input",
    )


def _users_confirm_options(parser):
    """Options of ``users confirm``: the preview's token."""
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from users preview, or - for standard input",
    )


def _users_import_options(parser):
    """Options of ``users import-leaders``: preview whom, or confirm a review."""
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--preview",
        action="store_true",
        help="list the leaders without a user and review importing the chosen ones",
    )
    mode.add_argument(
        "--confirm", action="store_true", help="apply a reviewed import (--token)"
    )
    parser.add_argument(
        "--all", action="store_true", help="with --preview: import every leader listed"
    )
    parser.add_argument(
        "--email",
        action="append",
        help="with --preview: one leader's address to import (repeatable)",
    )
    parser.add_argument(
        "--token",
        help="with --confirm: preview.token from --preview, or - for standard input",
    )


def _users_list_options(parser):
    """Options of ``users list``: the Users table's sort (#952)."""
    parser.add_argument(
        "--sort",
        help="email, name, role or last_login; --sort=-NAME (with =) reverses "
        "it (default email; role and last_login start highest and newest)",
    )


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
        options=(_sessions_options,),
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
    from .system_health import (
        BackupNow,
        DebugAllow,
        DebugOff,
        RefreshAccept,
        RefreshAcceptPreview,
        SystemHealth,
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
            watch_subject="task_id",
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
            "List the current campaign's Family email history.",
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
        # System health (ADM-13 PR 2b): a read like the others above, so it
        # is catalogued with them (ADM-11 PR 3's read commands).
        CommandSpec(
            "system health",
            "Show the system's problems, services, mail senders and backups.",
            system_health,
            "read_only",
            False,
            SystemHealth.field_names(),
            3,
            options=(_watch_options,),
            audit_event="system_health_viewed",
            watch=True,
        ),
        # Take a backup now (ADM-13 PR 3b): keyed, fresh-gated, prompting.
        CommandSpec(
            "system backup-now",
            "Ask the server to take a backup now, as System health does.",
            system_backup_now,
            "full",
            True,
            BackupNow.field_names(),
            3,
            options=(_backup_now_options,),
            fresh_gated=True,
            prompts=True,
            request_key=True,
        ),
        # Turn off debug logging (ADM-13 PR 6b): keyed, fresh-gated, prompting.
        CommandSpec(
            "system debug-off",
            "Turn off debug logging in every service, as System health does.",
            system_debug_off,
            "full",
            True,
            DebugOff.field_names(),
            3,
            options=(_debug_off_options,),
            fresh_gated=True,
            prompts=True,
            request_key=True,
        ),
        # Accept this change once (ADM-13 PR 5b): the page's preview, then
        # its confirmation with the preview's token, fresh-gated, prompting
        # with the page's tick box. The preview changes nothing and records
        # nothing, as the page's preview step records nothing.
        CommandSpec(
            "system refresh-accept-preview",
            "Review accepting a refused large ParishSoft change once, as "
            "System health does.",
            system_refresh_accept_preview,
            "full",
            False,
            RefreshAcceptPreview.field_names(),
            3,
            audit_event=None,
        ),
        CommandSpec(
            "system refresh-accept",
            "Accept a reviewed refused ParishSoft change once and ask for a full "
            "refresh, as System health does.",
            system_refresh_accept,
            "full",
            True,
            RefreshAccept.field_names(),
            3,
            options=(_refresh_accept_options,),
            fresh_gated=True,
            prompts=True,
        ),
        # Allow debug logging again (ADM-13 PR 6c): Testing only, keyed by
        # the switch it clears, fresh-gated, prompting.
        CommandSpec(
            "system debug-allow",
            "Clear the debug-off switch in Testing, as System health does.",
            system_debug_allow,
            "full",
            True,
            DebugAllow.field_names(),
            3,
            options=(_debug_allow_options,),
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "go-live progress",
            "Show the Production activation page.",
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


def _change_specs():
    """The schedule change commands and configuration request status (PR 4).

    ``config request cancel`` (#944) is the operator's way out of a live
    end-date change stuck before it applied.
    """
    from .admin_changes import ConfigCancel, ScheduleConfirm, SchedulePreview
    from .admin_reads import ConfigRequest

    return (
        CommandSpec(
            "schedule preview",
            "Review a change to mail schedules or campaign dates.",
            schedule_preview,
            "full",
            False,
            SchedulePreview.field_names(),
            4,
            options=(_schedule_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "schedule confirm",
            "Confirm a reviewed schedule change.",
            schedule_confirm,
            "full",
            True,
            ScheduleConfirm.field_names(),
            4,
            options=(_schedule_confirm_options,),
        ),
        CommandSpec(
            "config request show",
            "Show the status of one of your configuration requests.",
            config_request_show,
            "read_only",
            False,
            ConfigRequest.field_names(),
            4,
            options=(_config_request_options,),
            audit_event=None,
            watch=True,
            watch_subject="request_id",
        ),
        CommandSpec(
            "config request cancel",
            "Cancel a live end-date change that has not applied.",
            config_request_cancel,
            "full",
            True,
            ConfigCancel.field_names(),
            # In the change commands' group (ADM-11 PR 4); added by #944.
            4,
            options=(_config_cancel_options,),
            prompts=True,
        ),
    )


def _operation_specs():
    """The operations commands (PR 9): task retry and the deliveries."""
    from .admin_operations import (
        BounceReport,
        DeliveryList,
        DeliveryResolve,
        DeliveryShow,
        RefusalClear,
        RefusalList,
        RefusalShow,
        TaskRetry,
    )

    return (
        CommandSpec(
            "task retry",
            "Retry a failed background task, as its page's Retry does.",
            task_retry,
            "full",
            True,
            TaskRetry.field_names(),
            9,
            options=(_task_retry_options,),
            request_key=True,
        ),
        CommandSpec(
            "delivery list",
            "List Outgoing mail, without recipients.",
            delivery_list,
            "read_only",
            False,
            DeliveryList.field_names(),
            9,
            options=(_delivery_list_options,),
            audit_event="delivery_viewed",
        ),
        CommandSpec(
            "delivery show",
            "Show one delivery, its history and the resolutions offered.",
            delivery_show,
            "read_only",
            False,
            DeliveryShow.field_names(),
            9,
            options=(_delivery_show_options,),
            audit_event="delivery_viewed",
        ),
        CommandSpec(
            "delivery resolve",
            "Resolve one delivery, as its page's form does.",
            delivery_resolve,
            "full",
            True,
            DeliveryResolve.field_names(),
            9,
            options=(_delivery_resolve_options,),
            request_key=True,
            expected_version=True,
        ),
        CommandSpec(
            "delivery resend",
            "Resend one uncertain delivery, accepting the duplicate risk.",
            delivery_resend,
            "full",
            True,
            DeliveryResolve.field_names(),
            9,
            options=(_delivery_resend_options,),
            prompts=True,
            request_key=True,
            expected_version=True,
        ),
        CommandSpec(
            "delivery refusals",
            "List unresolved refused addresses, without the addresses.",
            delivery_refusals,
            "read_only",
            False,
            RefusalList.field_names(),
            9,
            options=(_refusal_list_options,),
            audit_event="delivery_viewed",
        ),
        CommandSpec(
            "delivery refusal-show",
            "Show one refused address's record and the source to verify.",
            delivery_refusal_show,
            "read_only",
            False,
            RefusalShow.field_names(),
            9,
            options=(_refusal_show_options,),
            audit_event="delivery_viewed",
        ),
        CommandSpec(
            "delivery refusal-clear",
            "Clear a refused address you verified, as its page does.",
            delivery_refusal_clear,
            "full",
            True,
            RefusalClear.field_names(),
            9,
            options=(_refusal_clear_options,),
            prompts=True,
            request_key=True,
        ),
        # The Mail message page's "Report a bounced address" (#806, #1211):
        # asks for the page's tick, as refusal-clear does; clearing undoes it.
        CommandSpec(
            "delivery bounce-report",
            "Report that a delivered email bounced at one address, as its page does.",
            delivery_bounce_report,
            "full",
            True,
            BounceReport.field_names(),
            9,
            options=(_bounce_report_options,),
            prompts=True,
            request_key=True,
        ),
    )


def _refresh_specs():
    """The manual ParishSoft refresh commands (PR 6a)."""
    from .admin_refresh import RefreshStart, RefreshStatus

    return (
        CommandSpec(
            "refresh start",
            "Ask for a full ParishSoft refresh now, as the Refresh from ParishSoft "
            "page does.",
            refresh_start,
            "full",
            True,
            RefreshStart.field_names(),
            6,
            options=(_refresh_start_options,),
            request_key=True,
        ),
        CommandSpec(
            "refresh status",
            "Show whether a refresh is running or waiting, and the latest ones.",
            refresh_status,
            "read_only",
            False,
            RefreshStatus.field_names(),
            6,
            audit_event=None,
        ),
    )


def _test_specs():
    """The Testing send commands: sample (PR 6b) and chosen-Family (PR 6c)."""
    from .admin_tests import (
        FamiliesPreview,
        FamiliesStatus,
        FamiliesTest,
        SamplePreview,
        SampleTest,
    )

    return (
        CommandSpec(
            "test sample-preview",
            "Review a sample test email for one email revision.",
            sample_preview,
            "full",
            False,
            SamplePreview.field_names(),
            6,
            options=(_sample_preview_options,),
            audit_event=None,
        ),
        CommandSpec(
            "test sample",
            "Send a reviewed sample test email to the Testing recipient.",
            sample_test,
            "full",
            True,
            SampleTest.field_names(),
            6,
            options=(_sample_test_options,),
            prompts=True,
        ),
        CommandSpec(
            "test families-preview",
            "Review a test of one email for up to ten chosen Families.",
            families_preview,
            "full",
            False,
            FamiliesPreview.field_names(),
            6,
            options=(_families_preview_options,),
            # The review itself changes nothing; --names requests an export
            # and records this event (#817). The catalog has no per-option
            # state, so changes_state stays False for the plain review and
            # the named audit event marks the --names exception (see the
            # spec's "Discovering commands").
            audit_event="admin_cmd_export_family_test_names",
        ),
        CommandSpec(
            "test families",
            "Send a reviewed chosen-Family test to the Testing recipient.",
            families_test,
            "full",
            True,
            FamiliesTest.field_names(),
            6,
            options=(_sample_test_options,),
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "test status",
            "Show the recent chosen-Family tests, without DUIDs.",
            families_status,
            "read_only",
            False,
            FamiliesStatus.field_names(),
            6,
            audit_event=None,
        ),
    )


def _portal_specs():
    """The Family portal maintenance switch (PR 7a, ``admin_portal``).

    With the Pause and resume mail page's read (PR 7b,
    ``admin_delivery_control``): like ``portal maintenance show``, any
    session runs it and it records nothing, as the page's GET records
    nothing. The pause, resume and resolution commands come later in PR 7b.
    """
    from .admin_delivery_control import DeliveryControls
    from .admin_portal import MaintenanceStatus

    return (
        CommandSpec(
            "portal maintenance show",
            "Show whether the Family portal is closed for maintenance.",
            portal_maintenance_show,
            "read_only",
            False,
            MaintenanceStatus.field_names(),
            7,
            audit_event=None,
        ),
        CommandSpec(
            "portal maintenance set",
            "Close the Family portal for maintenance, or reopen it.",
            portal_maintenance_set,
            "full",
            True,
            MaintenanceStatus.field_names(),
            7,
            options=(_portal_maintenance_set_options,),
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "send controls",
            "Show whether live mail is paused, and what is queued or held.",
            send_controls,
            "read_only",
            False,
            DeliveryControls.field_names(),
            7,
            audit_event=None,
        ),
        # --- Pause and resume mail's controls (PR 7b, #463) ---
        *_send_control_specs(),
    )


def _send_control_specs():
    """Pause and resume mail's previews and confirmation (PR 7b, #463).

    The previews record activity as the page's preview posts do, so they
    need a full-scope session, and record no event. ``send confirm`` is
    fresh-gated on the page, so it always prompts.
    """
    from .admin_delivery_control import ControlConfirm, ControlPreview

    preview = ControlPreview.field_names()
    return (
        CommandSpec(
            "send pause-preview",
            "Review pausing live Family email.",
            send_pause_preview,
            "full",
            False,
            preview,
            7,
            options=(_send_reason_options,),
            audit_event=None,
        ),
        CommandSpec(
            "send resume-preview",
            "Review resuming live Family email.",
            send_resume_preview,
            "full",
            False,
            preview,
            7,
            options=(_send_reason_options,),
            audit_event=None,
        ),
        CommandSpec(
            "send resolve-preview",
            "Review resolving a closed campaign's held messages.",
            send_resolve_preview,
            "full",
            False,
            preview,
            7,
            options=(_send_resolve_options, _send_reason_options),
            audit_event=None,
        ),
        CommandSpec(
            "send confirm",
            "Confirm a reviewed pause, resume or held-message resolution.",
            send_confirm,
            "full",
            True,
            ControlConfirm.field_names(),
            7,
            options=(_send_confirm_options,),
            fresh_gated=True,
            prompts=True,
        ),
    )


# --- end of Pause and resume mail's controls ----------------------------------


def _file_specs():
    """The hosted file library (PR 10, ``admin_hosted_files``) and content reads.

    The content reads (``admin_content``) are the Pages and emails, content
    editor and Content history pages' reads; like ``files list``, any session runs
    them and they record nothing, as the pages' GETs record nothing.
    """
    from .admin_content import (
        ContentConfirm,
        ContentHistory,
        ContentList,
        ContentPreview,
        ContentShow,
    )
    from .admin_hosted_files import FileDeletion, FileRename, HostedFileLibrary

    return (
        CommandSpec(
            "content list",
            "List the current campaign's pages and emails and their state.",
            content_list,
            "read_only",
            False,
            ContentList.field_names(),
            10,
            audit_event=None,
        ),
        CommandSpec(
            "content show",
            "Show one page's or email's text as the content editor opens it.",
            content_show,
            "read_only",
            False,
            ContentShow.field_names(),
            10,
            options=(_content_show_options,),
            audit_event=None,
        ),
        CommandSpec(
            "content history",
            "List the current campaign's retained pages and emails.",
            content_history,
            "read_only",
            False,
            ContentHistory.field_names(),
            10,
            options=(_content_history_options,),
            audit_event=None,
        ),
        # The email tables' Delete, split into a preview and a confirm as
        # schedule preview / schedule confirm split a schedule's Delete,
        # and the editors' plain-text preview. Full scope, as the Delete
        # post records activity; no recent sign-in on the page, so neither
        # is fresh-gated, and the reviewed token stands in for the dialog,
        # so neither prompts.
        CommandSpec(
            "content preview",
            "Build a delete of unused emails, or preview HTML as plain text.",
            content_preview,
            "full",
            False,
            ContentPreview.field_names(),
            10,
            options=(_content_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "content confirm",
            "Confirm a built delete of unused emails.",
            content_confirm,
            "full",
            True,
            ContentConfirm.field_names(),
            10,
            options=(_content_confirm_options,),
        ),
        # The page's GET records no event.
        CommandSpec(
            "files list",
            "List the hosted files, their links and where each is used.",
            files_list,
            "read_only",
            False,
            HostedFileLibrary.field_names(),
            10,
            audit_event=None,
        ),
        # The page's row actions: no recent sign-in on the page, so neither
        # is fresh-gated; a deletion cannot be undone, so it prompts.
        CommandSpec(
            "files delete",
            "Delete unused hosted files; their links stop working.",
            files_delete,
            "full",
            True,
            FileDeletion.field_names(),
            10,
            options=(_files_delete_options,),
            prompts=True,
        ),
        CommandSpec(
            "files rename",
            "Change an unused hosted file's placeholder name.",
            files_rename,
            "full",
            True,
            FileRename.field_names(),
            10,
            options=(_files_rename_options,),
        ),
    )


def _settings_specs():
    """The configuration pages' reads (PR 10, ``admin_settings``).

    Parish settings, Ministries, Parish logos and Share options: like
    ``files list``, any session runs them and they record nothing, as the
    pages' GETs record nothing. The parish profile's Review and Apply are
    ``parish preview`` and ``parish confirm`` (``admin_parish``), as ``users
    preview`` and ``users confirm``: full scope, as the page's posts record
    activity; neither is fresh-gated nor prompts, as the page asks for no
    recent sign-in and its Apply is the reviewed token. The other pages'
    changes come later in PR 10.
    """
    from .admin_parish import ParishConfirm, ParishPreview
    from .admin_settings import BrandingShow, CampaignShares, MinistryList, ParishShow

    def read(name, summary, handler, model, options=()):
        """One read's catalog entry: read-only, no state change, no event."""
        return CommandSpec(
            name,
            summary,
            handler,
            "read_only",
            False,
            model.field_names(),
            10,
            options=options,
            audit_event=None,
        )

    return (
        read("parish show", "Show the parish profile.", parish_show, ParishShow),
        CommandSpec(
            "parish preview",
            "Review a change to the parish profile.",
            parish_preview,
            "full",
            False,
            ParishPreview.field_names(),
            10,
            options=(_parish_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "parish confirm",
            "Confirm a reviewed change to the parish profile.",
            parish_confirm,
            "full",
            True,
            ParishConfirm.field_names(),
            10,
            options=(_parish_confirm_options,),
        ),
        read(
            "ministries list",
            "List the Ministry catalog and whether each is in the campaign.",
            ministries_list,
            MinistryList,
        ),
        read(
            "branding show",
            "Show the current parish logos.",
            branding_show,
            BrandingShow,
        ),
        read(
            "campaign shares",
            "Show a campaign's Share options.",
            campaign_shares,
            CampaignShares,
            options=(_schedule_options,),
        ),
    )


def _workgroup_specs():
    """The No-reminders WorkGroup page (PR 10, ``admin_workgroup``).

    Like ``parish show``, any session runs ``workgroup show`` and it records
    nothing, as the page's GET records nothing. The page's Review and Apply
    are ``workgroup preview`` and ``workgroup confirm``, as ``parish
    preview`` and ``parish confirm``: full scope, as the page's posts record
    activity; neither is fresh-gated nor prompts, as the page asks for no
    recent sign-in and its Apply is the reviewed token.
    """
    from .admin_workgroup import WorkGroupConfirm, WorkGroupPreview, WorkGroupShow

    return (
        CommandSpec(
            "workgroup show",
            "Show the current campaign's No-reminders WorkGroup.",
            workgroup_show,
            "read_only",
            False,
            WorkGroupShow.field_names(),
            10,
            audit_event=None,
        ),
        CommandSpec(
            "workgroup preview",
            "Review a change to the No-reminders WorkGroup.",
            workgroup_preview,
            "full",
            False,
            WorkGroupPreview.field_names(),
            10,
            options=(_workgroup_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "workgroup confirm",
            "Confirm a reviewed change to the No-reminders WorkGroup.",
            workgroup_confirm,
            "full",
            True,
            WorkGroupConfirm.field_names(),
            10,
            options=(_workgroup_confirm_options,),
        ),
    )


def _campaign_specs():
    """Campaign settings (PR 10, ``admin_campaign``).

    Like ``parish show``, any session runs ``campaign show`` and it records
    nothing, as the page's GET records nothing. The page's Review and Apply
    are ``campaign preview`` and ``campaign confirm``, as ``parish
    preview`` and ``parish confirm``: full scope, as the page's posts record
    activity; neither is fresh-gated nor prompts, as the page asks for no
    recent sign-in and its Apply is the reviewed token.
    """
    from .admin_campaign import CampaignConfirm, CampaignPreview, CampaignShow

    return (
        CommandSpec(
            "campaign show",
            "Show the current campaign's settings.",
            campaign_show,
            "read_only",
            False,
            CampaignShow.field_names(),
            10,
            audit_event=None,
        ),
        CommandSpec(
            "campaign preview",
            "Review a change to the current campaign's settings.",
            campaign_preview,
            "full",
            False,
            CampaignPreview.field_names(),
            10,
            options=(_campaign_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "campaign confirm",
            "Confirm a reviewed change to the current campaign's settings.",
            campaign_confirm,
            "full",
            True,
            CampaignConfirm.field_names(),
            10,
            options=(_campaign_confirm_options,),
        ),
    )


def _artwork_specs():
    """The Campaign images page's read and removal (PR 10, ``admin_artwork``).

    Like ``branding show``, any session runs ``artwork show`` and it records
    nothing, as the page's GET records nothing. Remove this image and its
    Apply are ``artwork remove-preview`` and ``artwork remove``, as ``parish
    preview`` and ``parish confirm``: full scope, as the page's requests
    record activity; neither is fresh-gated nor prompts, as the page asks
    for no recent sign-in and its Apply is the reviewed token.
    """
    from .admin_artwork import ArtworkRemove, ArtworkRemovePreview, ArtworkShow

    return (
        CommandSpec(
            "artwork show",
            "Show the current campaign's banner and page icons.",
            artwork_show,
            "read_only",
            False,
            ArtworkShow.field_names(),
            10,
            audit_event=None,
        ),
        CommandSpec(
            "artwork remove-preview",
            "Review removing one of the current campaign's images.",
            artwork_remove_preview,
            "full",
            False,
            ArtworkRemovePreview.field_names(),
            10,
            options=(_artwork_remove_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "artwork remove",
            "Remove one of the current campaign's images, as reviewed.",
            artwork_remove,
            "full",
            True,
            ArtworkRemove.field_names(),
            10,
            options=(_artwork_remove_options,),
        ),
    )


def _integration_specs():
    """The Integrations pages' reads (PR 10, ``admin_integrations``).

    Like ``parish show``, any session runs them and they record nothing, as
    the pages' GETs record nothing. ``integration status`` takes ``--watch``,
    as the settings page follows its status line while a change runs.
    ``integration schedule-preview`` is the ParishSoft page's live schedule
    check, which records nothing either. A settings page's Save and Remove
    are ``integration preview`` and ``integration confirm``
    (``admin_integration_changes``), as ``parish preview`` and ``parish
    confirm``: full scope, as the page's posts record activity. The page
    asks for a recent sign-in (#547), so ``integration confirm`` is
    fresh-gated and prompts; the preview records nothing.
    ``integration backup-probe`` (Test access) and ``integration dismiss``
    (a key change line's Dismiss) are full scope and neither fresh-gated
    nor prompting, as those pages ask for neither
    (``admin_integration_actions``). A key change's status page and Finish
    switching are ``integration key status`` (any session, records nothing)
    and ``integration key finish-switching-preview`` and
    ``integration key finish-switching`` (``admin_integration_keys``), the
    confirmation fresh-gated and prompting, as ``integration confirm``.
    Replacing a key comes later in PR 10.
    """
    from .admin_integration_actions import BackupProbe, KeyDismissal
    from .admin_integration_changes import IntegrationConfirm, IntegrationPreview
    from .admin_integration_keys import FinishConfirm, FinishPreview, KeyStatus
    from .admin_integrations import (
        IntegrationList,
        IntegrationShow,
        IntegrationStatus,
        SchedulePreview,
    )

    return (
        CommandSpec(
            "integration list",
            "List the integrations and whether each is set up.",
            integration_list,
            "read_only",
            False,
            IntegrationList.field_names(),
            10,
            audit_event=None,
        ),
        CommandSpec(
            "integration show",
            "Show one integration's settings and status.",
            integration_show,
            "read_only",
            False,
            IntegrationShow.field_names(),
            10,
            options=(_integration_show_options,),
            audit_event=None,
        ),
        CommandSpec(
            "integration status",
            "Show whether an integration's key change or folder check is running.",
            integration_status,
            "read_only",
            False,
            IntegrationStatus.field_names(),
            10,
            options=(_integration_status_options,),
            audit_event=None,
            watch=True,
        ),
        CommandSpec(
            "integration schedule-preview",
            "Preview the ParishSoft refresh schedule's next seven days and cost.",
            integration_schedule_preview,
            "read_only",
            False,
            SchedulePreview.field_names(),
            10,
            options=(_integration_schedule_preview_options,),
            audit_event=None,
        ),
        CommandSpec(
            "integration preview",
            "Review a change to an integration's settings, or its removal.",
            integration_preview,
            "full",
            False,
            IntegrationPreview.field_names(),
            10,
            options=(_integration_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "integration confirm",
            "Apply a reviewed change to an integration's settings.",
            integration_confirm,
            "full",
            True,
            IntegrationConfirm.field_names(),
            10,
            options=(_integration_confirm_options,),
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "integration backup-probe",
            "Check that off-site backups can be saved in a Google Drive folder.",
            integration_backup_probe,
            "full",
            True,
            BackupProbe.field_names(),
            10,
            options=(_integration_backup_probe_options,),
        ),
        CommandSpec(
            "integration dismiss",
            "Dismiss the result of a finished key change.",
            integration_dismiss,
            "full",
            True,
            KeyDismissal.field_names(),
            10,
            options=(_integration_dismiss_options,),
        ),
        CommandSpec(
            "integration key status",
            "Show the progress of one of your integration key changes.",
            integration_key_status,
            "read_only",
            False,
            KeyStatus.field_names(),
            10,
            options=(_integration_key_status_options,),
            audit_event=None,
            watch=True,
            watch_subject="request_id",
        ),
        CommandSpec(
            "integration key finish-switching-preview",
            "Review finishing the switch to an integration's new key.",
            integration_key_finish_switching_preview,
            "full",
            False,
            FinishPreview.field_names(),
            10,
            options=(_integration_key_target_options,),
            audit_event=None,
        ),
        CommandSpec(
            "integration key finish-switching",
            "Finish switching an integration to its new key.",
            integration_key_finish_switching,
            "full",
            True,
            FinishConfirm.field_names(),
            10,
            options=(_integration_key_finish_options,),
            fresh_gated=True,
            prompts=True,
        ),
    )


def _history_specs():
    """Past campaigns (#1018): the archive import, the page's actions.

    Reading needs any session; Review stores a pending preview, so it needs
    a full-scope session and records its command event. Apply asks only to
    replace a year; Remove always asks.
    """
    from .admin_history import (
        HistoryConfirm,
        HistoryList,
        HistoryPreview,
        HistoryRemove,
    )

    return (
        CommandSpec(
            "history list",
            "List the imported past campaigns and their removals.",
            history_list,
            "read_only",
            False,
            HistoryList.field_names(),
            8,
            audit_event=None,
        ),
        CommandSpec(
            "history preview",
            "Check one past campaign's archive workbook from standard input.",
            history_preview,
            "full",
            True,
            HistoryPreview.field_names(),
            8,
            options=(_history_preview_options,),
        ),
        CommandSpec(
            "history confirm",
            "Import a checked archive workbook, replacing its year if asked.",
            history_confirm,
            "full",
            True,
            HistoryConfirm.field_names(),
            8,
            options=(_history_confirm_options,),
            prompts=True,
        ),
        CommandSpec(
            "history remove",
            "Remove one past campaign's imported figures.",
            history_remove,
            "full",
            True,
            HistoryRemove.field_names(),
            8,
            options=(_history_remove_options,),
            prompts=True,
            expected_version=True,
        ),
    )


def _report_specs():
    """The report, export, digest and log commands (PR 8; 8a: the logs).

    PR 8b adds the export lifecycle (``admin_exports``), PR 8c the
    aggregate report reads (``admin_report_reads``), PR 8d the digests
    (``admin_digests``) and PR 8e the Family-level exports
    (``admin_family_exports``).
    """
    from .admin_census import CensusEdit, CensusReport, CensusResolve, CensusReview
    from .admin_census_export import CensusExport
    from .admin_data_to_fix import DataToFixReport
    from .admin_digests import DailyDigest, DigestList, WeeklyDigest, WeeklyRequest
    from .admin_exact_exports import ExactChange, ExactStatus
    from .admin_exports import ExportChange, ExportDownload, ExportStatus
    from .admin_limitations_export import LimitationsExport
    from .admin_report_reads import (
        ComparisonReport,
        FinancialReport,
        InformationReport,
        LimitationsReport,
        MinistryReport,
        ParticipationReport,
        ReportList,
        ResponseReport,
    )
    from .admin_reports import LogExport, LogList
    from .admin_roster import RosterReport
    from .admin_roster_export import RosterExport
    from .admin_source_form import SourceFormReport

    return (
        CommandSpec(
            "logs list",
            "List System logs entries, filtered as the page.",
            logs_list,
            "read_only",
            False,
            LogList.field_names(),
            8,
            options=(_logs_list_options,),
            audit_event="system_logs_viewed",
        ),
        CommandSpec(
            "logs export",
            "Write the System logs download to standard output.",
            logs_export,
            "full",
            False,
            LogExport.field_names(),
            8,
            options=(_logs_export_options,),
            audit_event="system_logs_exported",
            streams=True,
        ),
        CommandSpec(
            "export create",
            "Request a participation export, as the page's export form does.",
            export_create,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_create_options,),
            request_key=True,
        ),
        # The Campaign comparison's download form (#1018, slice 6).
        CommandSpec(
            "export comparison",
            "Request a Campaign comparison download, as its page's form does.",
            export_comparison,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_comparison_options,),
            request_key=True,
        ),
        CommandSpec(
            "export status",
            "Show one export's state, as its status page does.",
            export_status,
            "read_only",
            False,
            ExportStatus.field_names(),
            8,
            options=(_export_status_options,),
            watch=True,
            audit_event=None,
        ),
        CommandSpec(
            "export cancel",
            "Cancel an export that is not ready yet.",
            export_cancel,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_id_option,),
        ),
        CommandSpec(
            "export retry",
            "Retry an export whose latest run failed.",
            export_retry,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_keyed_options,),
            request_key=True,
        ),
        CommandSpec(
            "export regenerate",
            "Request an expired export again from its retained inputs.",
            export_regenerate,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_keyed_options,),
            request_key=True,
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "export download",
            "Write an export's file to standard output, as its download does.",
            export_download,
            "full",
            False,
            ExportDownload.field_names(),
            8,
            options=(_export_download_options,),
            audit_event="export_downloaded",
            streams=True,
        ),
        # The Family-level exports (PR 8e, ``admin_family_exports``): each
        # creates an export record, fetched with ``export fetch``.
        # The campaign archive workbook (#1018 part (a)): Past campaigns'
        # Download campaign archive.
        CommandSpec(
            "export archive",
            "Request the campaign archive workbook, as Past campaigns does.",
            export_archive,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_request_key_option,),
            request_key=True,
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "export financial",
            "Request a financial report export, as its export form does.",
            export_financial,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_family_export_options,),
            request_key=True,
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "export information",
            "Request an additional information export, as its form does.",
            export_information,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_information_options,),
            request_key=True,
        ),
        CommandSpec(
            "export ministry",
            "Request a Ministry report export, as its export form does.",
            export_ministry,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_ministry_options,),
            request_key=True,
        ),
        CommandSpec(
            "export ministry-packet",
            "Request a Ministry follow-up packet, as its packet form does.",
            export_ministry_packet,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_ministry_packet_options,),
            request_key=True,
        ),
        # The Family directory's exports (PR 8f): the code MAC keyring loads
        # only for a new request of these two.
        CommandSpec(
            "export directory",
            "Request a Family-code directory export, as its form does.",
            export_directory,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_family_export_options,),
            request_key=True,
            fresh_gated=True,
            prompts=True,
        ),
        CommandSpec(
            "export postal",
            "Request a postal mail-merge export, as its form does.",
            export_postal,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_family_export_options,),
            request_key=True,
            fresh_gated=True,
            prompts=True,
        ),
        # The exact daily exports (PR 8h, ``admin_exact_exports``).
        CommandSpec(
            "export exact create",
            "Request an exact daily export, as the Participation page does.",
            export_exact_create,
            "full",
            True,
            ExactChange.field_names(),
            8,
            options=(_exact_create_options,),
            request_key=True,
        ),
        CommandSpec(
            "export exact status",
            "Show one exact daily export's state, as its status page does.",
            export_exact_status,
            "read_only",
            False,
            ExactStatus.field_names(),
            8,
            options=(_exact_status_options,),
            watch=True,
            audit_event=None,
        ),
        CommandSpec(
            "export exact cancel",
            "Cancel an exact daily export that is not ready yet.",
            export_exact_cancel,
            "full",
            True,
            ExactChange.field_names(),
            8,
            options=(_exact_id_option,),
        ),
        CommandSpec(
            "export exact retry",
            "Retry an exact daily export whose latest run failed.",
            export_exact_retry,
            "full",
            True,
            ExactChange.field_names(),
            8,
            options=(_exact_retry_options,),
            request_key=True,
        ),
        # The one-Family timeline export (PR 8g, migration 0063).
        CommandSpec(
            "export family-timeline",
            "Request one Family's timeline export, as its page's form does.",
            export_family_timeline,
            "full",
            True,
            ExportChange.field_names(),
            8,
            options=(_export_family_timeline_options,),
            request_key=True,
            fresh_gated=True,
            prompts=True,
        ),
        # The digests (PR 8d, ``admin_digests``), and the Emailed reports
        # page that lists them.
        CommandSpec(
            "digest list",
            "List the retained daily and weekly reports, as Emailed reports.",
            digest_list,
            "read_only",
            False,
            DigestList.field_names(),
            8,
            options=(_digest_list_options,),
            audit_event=None,
        ),
        CommandSpec(
            "digest daily",
            "Show a retained daily report's figures.",
            digest_daily,
            "read_only",
            False,
            DailyDigest.field_names(),
            8,
            options=(_snapshot_option,),
            audit_event="daily_digest_viewed",
        ),
        CommandSpec(
            "digest weekly",
            "Show a retained weekly report's items, without Family text.",
            digest_weekly,
            "read_only",
            False,
            WeeklyDigest.field_names(),
            8,
            options=(_digest_weekly_options,),
            audit_event="weekly_digest_viewed",
        ),
        CommandSpec(
            "digest weekly-request",
            "Request a manual weekly report, as its page's form does.",
            digest_weekly_request,
            "full",
            True,
            WeeklyRequest.field_names(),
            8,
            options=(_digest_weekly_request_options,),
            request_key=True,
            prompts=True,
        ),
        # The aggregate report reads (PR 8c, ``admin_report_reads``).
        CommandSpec(
            "report list",
            "Show the current campaign and the reports you may read.",
            report_list,
            "read_only",
            False,
            ReportList.field_names(),
            8,
            audit_event=None,
        ),
        CommandSpec(
            "report participation",
            "Show the Participation report's statistics and daily counts.",
            report_participation,
            "read_only",
            False,
            ParticipationReport.field_names(),
            8,
            options=(_report_participation_options,),
            audit_event="participation_viewed",
        ),
        CommandSpec(
            "report comparison",
            "Show the Campaign comparison report's figures for a day.",
            report_comparison,
            "read_only",
            False,
            ComparisonReport.field_names(),
            8,
            options=(_report_comparison_options,),
            audit_event="campaign_comparison_viewed",
        ),
        CommandSpec(
            "report responses",
            "Show the response dashboard's counts.",
            report_responses,
            "read_only",
            False,
            ResponseReport.field_names(),
            8,
            options=(_report_responses_options,),
            audit_event="response_dashboard_viewed",
        ),
        CommandSpec(
            "report financial",
            "Show the Financial stewardship summary, without Family rows.",
            report_financial,
            "read_only",
            False,
            FinancialReport.field_names(),
            8,
            audit_event="financial_report_viewed",
        ),
        CommandSpec(
            "report limitations",
            "Show the Limitations summary, without Member or Family rows.",
            report_limitations,
            "read_only",
            False,
            LimitationsReport.field_names(),
            8,
            # The page keeps the event it recorded as Talents and limitations.
            audit_event="talents_report_viewed",
        ),
        CommandSpec(
            "report information",
            "Count the information queue items that match the filters.",
            report_information,
            "read_only",
            False,
            InformationReport.field_names(),
            8,
            options=(_report_information_options,),
            audit_event="information_viewed",
        ),
        CommandSpec(
            "report ministry",
            "Show the Ministry summary, or count one Ministry's requests.",
            report_ministry,
            "read_only",
            False,
            MinistryReport.field_names(),
            8,
            options=(_report_ministry_options,),
            audit_event="ministry_report_viewed",
        ),
        # Roster changes to enter (#528): counts only; its rows name
        # Members, so they stay on the page and its downloads.
        CommandSpec(
            "report roster",
            "Count the resolved Ministry changes to enter in ParishSoft.",
            report_roster,
            "read_only",
            False,
            RosterReport.field_names(),
            8,
            options=(_report_roster_options,),
            audit_event="roster_changes_viewed",
        ),
        # The Roster changes to enter download (PR 8g): the page's file.
        CommandSpec(
            "export roster",
            "Write the Roster changes to enter download to standard output.",
            export_roster,
            "full",
            False,
            RosterExport.field_names(),
            8,
            options=(_export_roster_options,),
            audit_event="roster_changes_exported",
            streams=True,
        ),
        # The Limitations download (PR 8h): the page's file.
        CommandSpec(
            "export limitations",
            "Write the Limitations download to standard output.",
            export_limitations,
            "full",
            False,
            LimitationsExport.field_names(),
            8,
            options=(_export_limitations_options,),
            # The page keeps the event it recorded as Talents and limitations.
            audit_event="talents_report_exported",
            streams=True,
        ),
        # The Census changes download (PR 8h): the page's file, or JSON
        # Lines with each change's id and version for ``census resolve``.
        CommandSpec(
            "export census",
            "Write the Census changes download to standard output.",
            export_census,
            "full",
            False,
            CensusExport.field_names(),
            8,
            options=(_export_census_options,),
            audit_event="census_changes_exported",
            streams=True,
        ),
        # Census changes (#528): counts only; its rows name Families and
        # Members with their contact details, so they stay on the page and
        # its downloads.
        CommandSpec(
            "report census",
            "Count the census changes Families reported, by status.",
            report_census,
            "read_only",
            False,
            CensusReport.field_names(),
            8,
            options=(_report_census_options,),
            audit_event="census_changes_viewed",
        ),
        # A row's Entered in ParishSoft, Ignore and Reopen (#528): the
        # page's service, keyed by the page's request key.
        CommandSpec(
            "census resolve",
            "Record a census change's outcome, as its row's actions do.",
            census_resolve,
            "full",
            True,
            CensusResolve.field_names(),
            8,
            options=(_census_resolve_options,),
            # Only Entered in ParishSoft, which cannot be undone, asks.
            prompts=True,
            request_key=True,
            expected_version=True,
        ),
        # An Automatic row's review (#528, ADM-09): the page's service,
        # keyed by the page's request key; reversible, so it asks nothing.
        CommandSpec(
            "census review",
            "Review an automatic census change, as its row's actions do.",
            census_review,
            "full",
            True,
            CensusReview.field_names(),
            8,
            options=(_census_review_options,),
            request_key=True,
            expected_version=True,
        ),
        # An Automatic row's Edit value and Clear edit (#1434, ADM-09.04):
        # the page's service, keyed; reversible, so it asks nothing.
        CommandSpec(
            "census edit",
            "Edit the value an automatic census change publishes, or clear it.",
            census_edit,
            "full",
            True,
            CensusEdit.field_names(),
            8,
            options=(_census_edit_options,),
            request_key=True,
            expected_version=True,
        ),
        # An Automatic Conflict's Use the Family's value anyway (#1434,
        # ADM-09.03): the page's service, keyed; census review undoes it, so
        # it asks nothing.
        CommandSpec(
            "census resolve-conflict",
            "Approve an automatic census conflict over ParishSoft's value.",
            census_resolve_conflict,
            "full",
            True,
            CensusReview.field_names(),
            8,
            options=(_census_change_options,),
            request_key=True,
            expected_version=True,
        ),
        # System health's Families the form cannot open (#774): counts and
        # DUIDs only, Administrators only, as the page.
        CommandSpec(
            "report source-form",
            "List the Families whose ParishSoft data keeps the form closed.",
            report_source_form,
            "read_only",
            False,
            SourceFormReport.field_names(),
            8,
            audit_event="source_form_viewed",
        ),
        # Data to fix in ParishSoft (#1011): section counts only, for the
        # sections the reader's role may see, as the page.
        CommandSpec(
            "report data-to-fix",
            "Count the data problems to fix in ParishSoft, by kind.",
            report_data_to_fix,
            "read_only",
            False,
            DataToFixReport.field_names(),
            8,
            audit_event="data_to_fix_viewed",
        ),
    )


def _users_specs():
    """The users commands (#952), in the users group (ADM-11 PR 11).

    ``users list`` and ``users preview-upgrade`` are passive reads any
    session may run; each records the page's view event,
    ``portal_users_viewed``. ``users preview`` and ``users confirm`` change
    users as the Users page does (slice 5) and need a full-scope session, as
    does ``users import-leaders``, the page's leaders table and import
    (slice 6).
    """
    from .admin_reads import UsersList, UsersUpgradePreview
    from .admin_users import LeaderImport, UsersConfirm, UsersPreview

    return (
        CommandSpec(
            "users list",
            "List the Admin portal users with their roles and last sign-in.",
            users_list,
            "read_only",
            False,
            UsersList.field_names(),
            11,
            options=(_users_list_options,),
            audit_event="portal_users_viewed",
        ),
        CommandSpec(
            "users preview-upgrade",
            "Show what the users upgrade would do with today's sign-in rules.",
            users_preview_upgrade,
            "read_only",
            False,
            UsersUpgradePreview.field_names(),
            11,
            audit_event="portal_users_viewed",
        ),
        CommandSpec(
            "users preview",
            "Review a change to Admin portal users.",
            users_preview,
            "full",
            False,
            UsersPreview.field_names(),
            11,
            options=(_users_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "users confirm",
            "Confirm a reviewed change to Admin portal users.",
            users_confirm,
            "full",
            True,
            UsersConfirm.field_names(),
            11,
            options=(_users_confirm_options,),
        ),
        CommandSpec(
            "users import-leaders",
            "Import ParishSoft Ministry leaders as users, after a preview.",
            users_import_leaders,
            "full",
            True,
            LeaderImport.field_names(),
            11,
            options=(_users_import_options,),
        ),
    )


def _events_specs():
    """Home's acknowledgements (ADM-11 PR 11, ``admin_events``).

    ``events list`` is a passive read any session may run; Home records no
    event. ``events acknowledge`` is the pages' Acknowledge, which asks for
    no recent sign-in and no confirmation, so it is neither fresh-gated nor
    prompting.
    """
    from .admin_events import EventsAcknowledgement, EventsList

    return (
        CommandSpec(
            "events list",
            "List the security events and critical problems to acknowledge.",
            events_list,
            "read_only",
            False,
            EventsList.field_names(),
            11,
            audit_event=None,
        ),
        CommandSpec(
            "events acknowledge",
            "Acknowledge a security event, or the critical problems listed.",
            events_acknowledge,
            "full",
            True,
            EventsAcknowledgement.field_names(),
            11,
            options=(_events_acknowledge_options,),
        ),
    )


# ---------------------------------------------- Ministry follow-up (PR 11a)
#
# ``followup list``, ``followup show``, ``followup update`` and ``followup
# bulk`` (#463, in ``admin_followup``): the Ministry requests follow-up
# queue, a request's page and Save, and the queue's bulk outcome.

# The queue's choices (``reports.ministry_followup``), spelled out because
# the parser is built before Django is set up; a test keeps them equal.
FOLLOWUP_STATES = (
    "unresolved",
    "any",
    "new",
    "in_progress",
    "resolved",
    "closed_no_response",
    "not_in_parishsoft",
)
FOLLOWUP_OUTCOMES = (
    "any",
    "joined",
    "leave_confirmed",
    "declined",
    "other",
    "no_response",
)
# What a Save may set (``workflows.models.STAFF_STATES``), the outcomes a
# Resolved request records (less the retired ``duplicate``), and the
# contact channels (``CONTACT_CHANNELS``).
FOLLOWUP_SAVE_STATES = ("new", "in_progress", "resolved", "closed_no_response")
FOLLOWUP_RESOLVED = ("joined", "leave_confirmed", "declined", "other")
FOLLOWUP_CHANNELS = ("email", "phone", "in_person", "other")
# The queue's bulk outcome buttons, by the page's posted "kind:outcome"
# value (``workflows.followup.BULK_ACTIONS``).
FOLLOWUP_BULK = (
    "join:joined",
    "join:declined",
    "leave:leave_confirmed",
    "leave:declined",
)
# The longest notes the page takes (``workflows.followup.MAX_NOTES``), in
# UTF-8 bytes at most four per character.
FOLLOWUP_NOTES_LIMIT = 5000 * 4
# The longest contact notes (``MAX_CONTACT_NOTES``), likewise in bytes.
FOLLOWUP_CONTACT_NOTES_LIMIT = 2000 * 4


def _positive(value):
    """A page number: a positive integer; anything else is a usage error."""
    if not value.isdecimal() or not 1 <= int(value) <= 2**31 - 1:
        raise argparse.ArgumentTypeError("not a page number")
    return int(value)


def _chosen_request(value):
    """``--request ID:VERSION``: a request and the version its row showed."""
    identity, separator, version = value.partition(":")
    if not separator:
        raise argparse.ArgumentTypeError("not ID:VERSION")
    try:
        return _uuid(identity), _version(version)
    except argparse.ArgumentTypeError:
        raise argparse.ArgumentTypeError("not ID:VERSION") from None


def followup_list(args, preamble, runtime, context):
    """One page of the Ministry follow-up queue, without Members (#463)."""
    from .admin_followup import read_followup_list

    return read_followup_list(
        context["caller"],
        runtime,
        {
            "ministry": args.ministry,
            "action": args.action,
            "state": args.state,
            "outcome": args.outcome,
            "sort": args.sort,
            "page": args.page,
            "size": args.size,
        },
    )


def followup_show(args, preamble, runtime, context):
    """One Ministry request and its follow-up history, without notes (#463)."""
    from .admin_followup import read_followup_show

    return read_followup_show(
        context["caller"], runtime, args.request_id, history_page=args.page or 1
    )


def _followup_contact(args, context, *, stdin_free=True):
    """The contact attempt's options, refused when they would have no effect.

    The page sends a contact attempt's instant and notes only with a
    channel; on the command line an instant or notes without
    ``--contact-channel`` would be silently dropped, so it is a usage error,
    as other options that go only with another are. ``--contact-notes -``
    reads standard input, when no other option already does.
    """
    if args.contact_channel is None and (
        args.contact_at is not None or args.contact_notes is not None
    ):
        raise UsageError("--contact-at and --contact-notes go with --contact-channel.")
    if args.contact_notes == "-" and not stdin_free:
        raise UsageError("Only one input may be read from standard input.")
    if args.contact_notes is None:
        return None
    return _input(args.contact_notes, context, FOLLOWUP_CONTACT_NOTES_LIMIT)


def followup_update(args, preamble, runtime, context):
    """Record follow-up on one Ministry request, as its Save does (#463).

    Without ``--request-key`` a new key is made and written to standard
    error before anything is done, so a run that crashes can be repeated
    with it. The page's Save asks nothing, so neither does this. An
    ``--outcome`` goes only with ``--state resolved``, as the page shows
    the outcome only then.
    """
    from .admin_followup import update_followup

    if args.outcome is not None and args.state != "resolved":
        raise UsageError("--outcome goes with --state resolved.")
    key = _request_key(args, context)
    notes = None
    if args.notes is not None:
        notes = _input(args.notes, context, FOLLOWUP_NOTES_LIMIT)
    contact_notes = _followup_contact(args, context, stdin_free=args.notes != "-")
    return update_followup(
        context["caller"],
        runtime,
        args.request_id,
        expected_version=args.expected_version,
        request_key=key,
        state=args.state,
        outcome=args.outcome,
        notes=notes,
        channel=args.contact_channel,
        contact_at=args.contact_at,
        contact_notes=contact_notes,
        context=context,
    )


def followup_bulk(args, preamble, runtime, context):
    """Resolve several Ministry requests with one outcome (#463, #1131).

    The queue's bulk outcome asks in its confirmation dialog, and resolving
    a request closes it, so this asks first (or takes ``--yes``), after
    refusing a choice too long to record. Without ``--request-key`` a new
    key is made and written to standard error before anything is done.
    """
    from . import admin_followup
    from .reports.ministries import outcome_label

    # The role first (#1341): the page's own admission, which admits Staff,
    # Administrators and a leader of any Ministry, before any input or
    # prompt; each chosen request is checked against the leader's scope.
    admin_followup.admit_followup(context["caller"], runtime)

    key = _request_key(args, context)
    contact_notes = _followup_contact(args, context)
    versions = dict(args.request)
    if len(versions) != len(args.request):
        raise ValueError("A request was chosen twice.")
    # Refused before the prompt: nothing is asked about a choice the page
    # could not record.
    admin_followup.check_lengths(contact_notes=contact_notes, requests=versions)
    kind, _, outcome = args.outcome.partition(":")
    # The page's dialog words: the outcome's label for this kind of request.
    confirm(
        context,
        (
            f"Mark {len(versions)} Ministry request(s) as "
            f"{outcome_label(outcome, kind)}. Each request is resolved, with "
            "its own entry in its follow-up history.",
            "A resolved request leaves the open queue and cannot be edited again.",
        ),
    )
    return admin_followup.bulk_followup(
        context["caller"],
        runtime,
        versions,
        action=kind,
        outcome=outcome,
        request_key=key,
        channel=args.contact_channel,
        contact_at=args.contact_at,
        contact_notes=contact_notes,
        context=context,
    )


def _followup_list_options(parser):
    """Options of ``followup list``: the queue's filters, without its search."""
    parser.add_argument("--ministry", metavar="DUID", help="one Ministry, by DUID")
    parser.add_argument("--action", choices=("any", "join", "leave"))
    parser.add_argument(
        "--state",
        choices=FOLLOWUP_STATES,
        help="default unresolved (New or In progress)",
    )
    parser.add_argument("--outcome", choices=FOLLOWUP_OUTCOMES)
    parser.add_argument(
        "--sort",
        choices=("newest", "name", "ministry"),
        help="default newest; name sorts by the Member, who is not printed",
    )
    parser.add_argument("--page", type=_positive, help="page number (default 1)")
    parser.add_argument(
        "--size", choices=("25", "50", "100", "250"), help="rows per page (default 50)"
    )


def _followup_show_options(parser):
    """Options of ``followup show``: the request and its history page."""
    parser.add_argument("request_id", type=_uuid, metavar="REQUEST_ID")
    parser.add_argument(
        "--page", type=_positive, help="the history's page number (default 1)"
    )


def _followup_contact_options(parser, *, prompts=False):
    """A follow-up's optional contact attempt, as the page's form takes it.

    ``prompts`` marks a prompting command, whose ``-`` input needs --yes.
    """
    parser.add_argument("--contact-channel", choices=FOLLOWUP_CHANNELS)
    parser.add_argument(
        "--contact-at",
        metavar="INSTANT",
        help="with --contact-channel: when, as an ISO 8601 instant with its offset",
    )
    parser.add_argument(
        "--contact-notes",
        help="with --contact-channel: optional notes on the contact attempt, "
        "or - to read them from standard input" + (" (needs --yes)" if prompts else ""),
    )


def _followup_update_options(parser):
    """Options of ``followup update``: a request page's form."""
    parser.add_argument("request_id", type=_uuid, metavar="REQUEST_ID")
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_version,
        help="the request's version, as followup show prints it",
    )
    parser.add_argument("--state", required=True, choices=FOLLOWUP_SAVE_STATES)
    parser.add_argument(
        "--outcome",
        choices=FOLLOWUP_RESOLVED,
        help="with --state resolved: the outcome (other needs --notes)",
    )
    parser.add_argument(
        "--notes",
        help="the request's notes, replacing them (default: kept), or - to "
        "read them from standard input",
    )
    _followup_contact_options(parser)
    _request_key_option(parser)


def _followup_bulk_options(parser):
    """Options of ``followup bulk``: the queue's bulk outcome dialog."""
    parser.add_argument(
        "--outcome",
        required=True,
        choices=FOLLOWUP_BULK,
        help="the request kind and outcome, as the queue's buttons post them",
    )
    parser.add_argument(
        "--request",
        required=True,
        action="append",
        type=_chosen_request,
        metavar="ID:VERSION",
        help="a chosen request and its version, as followup list prints them "
        "(repeat for each, up to 250)",
    )
    _followup_contact_options(parser, prompts=True)
    _request_key_option(parser)


def _followup_specs():
    """The Ministry follow-up commands (ADM-11 PR 11a, ``admin_followup``).

    ``followup list`` and ``followup show`` are passive reads any session
    may run; they record the page's ``ministry_followup_viewed``. ``followup
    update`` asks nothing, as the page's Save does not; ``followup bulk``
    asks, as the queue's bulk dialog does. Neither change is fresh-gated,
    as the page asks for no recent sign-in.
    """
    from .admin_followup import FollowupBulk, FollowupList, FollowupShow, FollowupUpdate

    return (
        CommandSpec(
            "followup list",
            "List a page of the Ministry follow-up queue, without Members.",
            followup_list,
            "read_only",
            False,
            FollowupList.field_names(),
            11,
            options=(_followup_list_options,),
            audit_event="ministry_followup_viewed",
        ),
        CommandSpec(
            "followup show",
            "Show one Ministry request and its follow-up history.",
            followup_show,
            "read_only",
            False,
            FollowupShow.field_names(),
            11,
            options=(_followup_show_options,),
            audit_event="ministry_followup_viewed",
        ),
        CommandSpec(
            "followup update",
            "Record follow-up on one Ministry request, as its Save does.",
            followup_update,
            "full",
            True,
            FollowupUpdate.field_names(),
            11,
            options=(_followup_update_options,),
            request_key=True,
            expected_version=True,
        ),
        CommandSpec(
            "followup bulk",
            "Resolve several Ministry requests with one outcome.",
            followup_bulk,
            "full",
            True,
            FollowupBulk.field_names(),
            11,
            options=(_followup_bulk_options,),
            prompts=True,
            request_key=True,
            expected_version=True,
        ),
    )


# ------------------------------------------ end of Ministry follow-up block


# ---------------------------------------------------- Share options (PR 10)
#
# ``campaign shares-preview`` and ``campaign shares-confirm`` (#463, in
# ``admin_shares``): Share options' Review and Apply. ``campaign shares``
# (in ``_settings_specs``) is the page's read.


def campaign_shares_preview(args, preamble, runtime, context):
    """Review a change to a campaign's Share options (PR 10)."""
    from .admin_shares import CHANGES_LIMIT, preview_shares

    # The role first (#1341): denied before any input is read.
    _role_first(context, runtime)
    return preview_shares(
        context["caller"],
        runtime,
        args.campaign,
        expected_version=args.expected_version,
        changes=_input(args.changes, context, CHANGES_LIMIT),
    )


def campaign_shares_confirm(args, preamble, runtime, context):
    """Confirm a reviewed Share options change as a request (PR 10)."""
    from .admin_changes import TOKEN_LIMIT
    from .admin_shares import confirm_shares

    # The role first (#1341): denied before any input is read.
    _role_first(context, runtime)
    return confirm_shares(
        context["caller"],
        runtime,
        args.campaign,
        token=_input(args.token, context, TOKEN_LIMIT),
        context=context,
    )


def _shares_preview_options(parser):
    """Options of ``campaign shares-preview``: the campaign, version and change."""
    _schedule_options(parser)
    parser.add_argument(
        "--expected-version",
        required=True,
        type=_digest,
        help="the version campaign shares printed",
    )
    parser.add_argument(
        "--changes",
        required=True,
        help="the change document as JSON, or - to read it from standard input",
    )


def _shares_confirm_options(parser):
    """Options of ``campaign shares-confirm``: the campaign and the token."""
    _schedule_options(parser)
    parser.add_argument(
        "--token",
        required=True,
        help="preview.token from campaign shares-preview, or - for standard input",
    )


def _share_specs():
    """Share options' Review and Apply (PR 10, ``admin_shares``).

    As ``campaign preview`` and ``campaign confirm``: full scope, as the
    page's posts record activity; neither is fresh-gated nor prompts, as
    the page asks for no recent sign-in and its Apply is the reviewed token.
    """
    from .admin_shares import SharesConfirm, SharesPreview

    return (
        CommandSpec(
            "campaign shares-preview",
            "Review a change to a campaign's Share options.",
            campaign_shares_preview,
            "full",
            False,
            SharesPreview.field_names(),
            10,
            options=(_shares_preview_options,),
            expected_version=True,
            audit_event=None,
        ),
        CommandSpec(
            "campaign shares-confirm",
            "Confirm a reviewed change to a campaign's Share options.",
            campaign_shares_confirm,
            "full",
            True,
            SharesConfirm.field_names(),
            10,
            options=(_shares_confirm_options,),
        ),
    )


# ------------------------------------------------- end of Share options block


COMMANDS = (
    COMMANDS
    + _read_specs()
    + _change_specs()
    + _refresh_specs()
    + _test_specs()
    + _portal_specs()
    + _file_specs()
    + _settings_specs()
    + _workgroup_specs()
    + _campaign_specs()
    + _share_specs()
    + _operation_specs()
    + _report_specs()
    + _users_specs()
    + _events_specs()
    + _followup_specs()
    + _history_specs()
    + _artwork_specs()
    + _integration_specs()
)
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
    """The subparser tree: a one-word command, ``<area> <verb>`` or deeper."""
    parser = _Parser(
        prog="pk-stewardship admin",
        description="Admin commands acting as an approved Administrator.",
    )
    # Each group of words before a command's last (an area such as
    # ``schedule``, or ``config request``) is a parser of its own, with its
    # own subparsers, created the first time a command names it.
    groups = {(): parser.add_subparsers(dest="area", parser_class=_Parser)}
    for spec in COMMANDS:
        words = tuple(spec.name.split(" "))
        for depth in range(1, len(words)):
            prefix = words[:depth]
            if prefix not in groups:
                group = groups[prefix[:-1]].add_parser(
                    prefix[-1], help=f"{' '.join(prefix)} commands"
                )
                groups[prefix] = group.add_subparsers(
                    dest=f"verb{depth}", parser_class=_Parser
                )
        command = groups[words[:-1]].add_parser(words[-1], help=spec.help)
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
        if spec.prompts:
            command.add_argument(
                "--yes",
                action="store_true",
                help="answer the confirmation prompt (required with a - input)",
            )
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
                "streams": spec.streams,
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
    if isinstance(error, (WatchTimeout, ConfirmationRequired)):
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


# The codes an unexpected error ends as. Their documents say only that much,
# and through the host wrapper the command's standard error reaches the
# operator's terminal, not the web container's log, so each also logs one
# failure line the operator can report (#612).
TRACED_CODES = frozenset({"internal", "outcome_unknown"})


def trace_failure(error, *, admitted_process):
    """Log one failure line for an unexpected error; never raise.

    The line is ``startup_rejected`` before admission finished and
    ``task_failed`` after it, with the invocation's ``correlation_id`` (also
    in the document, so the two join), the ``failure_kind`` category and the
    exception type's ``error_class``; never exception text. With debug
    logging on, ``emit_failure`` adds the traceback. It runs after admission
    has released the startup lease and closed its connections; the durable
    operational log entry for the same failure was written before that, by
    ``record_failure`` (#617).
    """
    from .observability import Event, emit_failure

    event = Event.TASK_FAILED if admitted_process else Event.STARTUP_REJECTED
    # The document and exit code are the command's contract; a failure to
    # log must not replace them with a traceback.
    with contextlib.suppress(Exception):
        emit_failure(error, event=event, name_class=True)


# The closed ``failure`` word each traced code records durably (#617).
FAILURE_WORDS = {
    "internal": "admin_command",
    "outcome_unknown": "admin_command_outcome_unknown",
}


def failure_context(spec, error, *, changed, committed):
    """The durable entry's sanitized ``failure`` context, or None.

    Only a failure that ends as ``internal`` or ``outcome_unknown``
    (``TRACED_CODES``, classified as ``run`` does after admission) has one.
    It holds closed values only: the ``FAILURE_WORDS`` word, the
    ``failure_kind`` category and the command's catalog name; never
    exception text, arguments, the session or personal data.
    """
    from .audit.schemas import ContextKind, sanitize
    from .observability import failure_kind_of

    code = classify(error, admitted_process=True, changed=changed, committed=committed)
    if code not in TRACED_CODES:
        return None
    return sanitize(
        ContextKind.FAILURE,
        {
            "failure": FAILURE_WORDS[code],
            "failure_kind": failure_kind_of(error),
            "command": spec.name,
        },
    )


def write_failure(context):
    """Insert one ERROR ``admin_command_failed`` row on a private connection.

    The command's own connections are closed first: its transaction may be
    aborted, and admission holds at most one connection. The row goes
    through a fresh, short-lived connection with the same (web) login and
    ``audit.timeouts``'s limits (2 s connect, 2 s statement, 1 s lock), so a
    slow or unreachable database cannot hold the command's exit. The entry
    carries the invocation's correlation id, which the document also shows.
    """
    from django.db import connections

    from .audit.schemas import ContextKind
    from .audit.timeouts import _private_connection
    from .observability import Event, current_correlation

    connections.close_all()
    db = _private_connection(None)
    try:
        with db.cursor() as cursor:
            cursor.execute("SET statement_timeout = '2s'")
            cursor.execute("SET lock_timeout = '1s'")
            cursor.execute(
                "INSERT INTO stewardship_operational_log "
                "(id,correlation_id,event,level,schema,context) "
                "VALUES (%s,%s,%s,'ERROR',%s,%s::jsonb)",
                [
                    uuid4(),
                    current_correlation(),
                    Event.ADMIN_COMMAND_FAILED.value,
                    ContextKind.FAILURE.value,
                    json.dumps(context),
                ],
            )
    finally:
        db.close()


def record_failure(spec, error, *, changed, committed):
    """Persist one operational log entry for an unexpected failure (#617).

    Called inside admission, after it finished, so the startup lease and the
    web's SQL login still hold. It never raises: a failure to build or write
    the entry logs one standard-error line (``admin_command_failed`` with
    its category), and the command's document and exit code stand.
    """
    try:
        context = failure_context(spec, error, changed=changed, committed=committed)
        if context is not None:
            write_failure(context)
    except Exception as write_error:
        # The command's own outcome is the contract; losing its log entry
        # must not change it.
        with contextlib.suppress(Exception):
            from .observability import Event, emit_failure

            emit_failure(
                write_error, event=Event.ADMIN_COMMAND_FAILED, level=logging.WARNING
            )


def refusal_record(spec, error, code, *, actor_id=None, admitted=True):
    """The ``RefusalRecord`` for a command refused with ``code`` (#1403).

    It is found as the page finds it (``web.refusal_log``), so both read
    alike in the system logs: an exit-1 refusal has the error's own closed
    reason (``web.refusals.reason_of``), else ``unexpected_fields`` when a
    problem names a key or id the caller sent that the command does not
    have, else ``unclassified`` with its class name; its code, then each
    field problem's code; and the global mode, read only after admission.

    ``fields`` is an allow-list: only a ``admin_changes.FieldProblem``'s
    server-owned ``logged`` name is recorded, never a problem's ``field``,
    which may hold a key, an id or a sheet name the caller sent. An
    ``unexpected`` problem is only counted (``unexpected_count``), as the
    page counts submitted names its form does not have. An exit-2 refusal
    has its code as its reason and nothing else. ``spec`` is None when no
    command was chosen. See the admin-automation spec, Refusal entry.
    """
    from .audit.refusals import RefusalRecord

    command = spec.name if spec is not None else None
    if EXIT_CODES[code] == 2:
        return RefusalRecord(reason=code, command=command)
    from .admin_changes import FieldProblem
    from .web.refusal_log import current_mode
    from .web.refusals import UserFacing, reason_of

    problems = [
        item for item in getattr(error, "fields", None) or () if isinstance(item, dict)
    ]
    marked = [item for item in problems if isinstance(item, FieldProblem)]
    unexpected = sum(1 for item in marked if item.unexpected)
    # Only a shared refusal carries closed setting values, as on the page.
    shared = error if isinstance(error, UserFacing) else None
    return RefusalRecord(
        reason=(
            reason_of(error) or ("unexpected_fields" if unexpected else "unclassified")
        ),
        actor_id=actor_id,
        command=command,
        fields=tuple(item.logged for item in marked if item.logged),
        codes=(code, *(item.get("code") for item in problems)),
        expected=getattr(shared, "expected", None),
        actual=getattr(shared, "actual", None),
        unexpected_count=unexpected or None,
        mode=current_mode() if admitted else None,
        error=error,
    )


def write_refusal(record):
    """Log one refused command, durably on a private connection; never raise.

    As for ``write_failure``, the command's own connections are closed
    first, and the entry goes through a fresh, short-lived connection with
    the same (web) login and a 2-second connect limit, so the process holds
    one connection at a time; ``record_refusal`` then writes the
    standard-error line and, for a named actor, the capped entry in its own
    short transaction, and closes nothing itself. Without an actor only the
    line is written and no connection is opened.
    """
    from django.db import connections

    from .audit.refusals import record_refusal
    from .audit.timeouts import _private_connection

    db, durable = None, True
    try:
        if record.actor_id is not None:
            connections.close_all()
            db = _private_connection(None)
    except Exception as error:
        # No private connection: the line still says what was refused.
        with contextlib.suppress(Exception):
            from .observability import Event, emit_failure

            emit_failure(error, event=Event.ADMIN_INPUT_REFUSED, level=logging.WARNING)
        # The line still names the actor; only the durable write is skipped.
        durable = False
    try:
        record_refusal(record, db=db, durable=durable)
    finally:
        if db is not None:
            with contextlib.suppress(Exception):
                db.close()


def record_command_refusal(spec, error, context, *, admitted, changed, committed):
    """Log one exit-1 or exit-2 refusal of ``spec``'s command; never raise.

    Called once per invocation, from the admitted command's own failure
    path (while admission still holds the web login), or, for a refusal
    before or outside it, from ``run``'s handler; ``context["refused"]``
    marks that it was done. ``error`` is classified as ``run`` classifies
    it; any other exit is not a refusal of input and records nothing. The
    actor is the session's Administrator (``context["actor_id"]``, set by
    ``admit_session``), only after admission and only for exit 1.
    """
    if context.get("refused"):
        return
    try:
        code = classify(
            error, admitted_process=admitted, changed=changed, committed=committed
        )
        if EXIT_CODES.get(code) not in {1, 2}:
            return
        context["refused"] = True
        actor_id = context.get("actor_id") if admitted else None
        write_refusal(
            refusal_record(spec, error, code, actor_id=actor_id, admitted=admitted)
        )
    except Exception as log_error:
        # The command's document and exit code are the contract.
        with contextlib.suppress(Exception):
            from .observability import Event, emit_failure

            emit_failure(
                log_error, event=Event.ADMIN_INPUT_REFUSED, level=logging.WARNING
            )


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
    # A streaming command's standard output holds the file alone.
    documents = stderr if spec.streams else stdout
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
            print(json.dumps(output, sort_keys=True), file=documents, flush=True)

        # "audit": only the first read of a --watch records the page's view.
        context = {
            "final": True,
            "audit": True,
            "stdin": stdin,
            "stdout": stdout,
            "stderr": stderr,
            "yes": bool(getattr(args, "yes", False)),
        }
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
                try:
                    if spec.scope != "none":
                        caller = admit_session(spec, preamble, runtime, stderr, context)
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
                except Exception as error:
                    # Record an unexpected failure durably while admission
                    # still holds the startup lease and the web login (#617);
                    # the handler below classifies it again, identically, for
                    # the document and exit code. record_failure never raises.
                    record_failure(
                        spec,
                        error,
                        changed=spec.changes_state and caller is not None,
                        committed=context.get("committed", False),
                    )
                    # A refusal of input, likewise, on the same kind of
                    # private connection (#1403); it never raises either.
                    record_command_refusal(
                        spec,
                        error,
                        context,
                        admitted=True,
                        changed=spec.changes_state and caller is not None,
                        committed=context.get("committed", False),
                    )
                    raise
            # ``logs export`` leaves its file's bytes for here; ``export
            # download`` has already written them inside its read guard.
            if spec.streams and "stream" in context:
                write_stream(stdout, context["stream"])
            emit(result, final=context["final"])
            return 0
        except Exception as error:
            code = classify(
                error,
                admitted_process=admitted_process,
                changed=spec.changes_state and caller is not None,
                committed=context.get("committed", False),
            )
            if code in TRACED_CODES:
                trace_failure(error, admitted_process=admitted_process)
            # A refusal before admission (exit 2) has its line here; one
            # inside the admitted command was recorded above.
            record_command_refusal(
                spec,
                error,
                context,
                admitted=admitted_process,
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
            if code == "invalid" and getattr(error, "fields", None):
                # The page's own messages for each field it refused.
                output["error"]["fields"] = error.fields
            if code == "outcome_unknown" and context.get("request_id"):
                # The request the command may have recorded, fixed before it
                # acted, so the operator can read it (config request show) or
                # repeat with it (task retry --request-key).
                output["error"]["request_id"] = context["request_id"]
            if isinstance(error, WatchTimeout) and error.model is not None:
                # The last state read, as the specification asks.
                output["result"] = error.model.to_document()
            print(json.dumps(output, sort_keys=True), file=documents, flush=True)
            return EXIT_CODES[code]


def write_stream(stdout, body):
    """Write a streamed file's bytes, exactly, to standard output.

    The bytes go to the binary buffer under a text stream, never through
    its encoding. A text-only stream (a test's ``StringIO``) gets them
    decoded as UTF-8, so only a text file can be written to one; a binary
    export (PNG, PDF, XLSX) needs a stream with a buffer.
    """
    target = getattr(stdout, "buffer", None)
    if target is None:
        stdout.write(body.decode("utf-8"))
        stdout.flush()
        return
    stdout.flush()
    target.write(body)
    target.flush()


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
                    # Which command, and which record, the watch followed.
                    watched_command=spec.name,
                    subject_id=(
                        getattr(args, spec.watch_subject)
                        if spec.watch_subject
                        else None
                    ),
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


def admit_session(spec, preamble, runtime, stderr, context):
    """Admit the command through its automation session and check its scope.

    The session's Administrator is kept as ``context["actor_id"]`` before the
    scope check, so a refusal from that check onwards names its actor in
    the refusal log (#1403).
    """
    from .accounts.admin_caller import AdminCaller
    from .accounts.automation_sessions import database_now

    caller = AdminCaller.from_automation(
        preamble.secret,
        preamble.host_digest,
        store=runtime.store,
        pairing=runtime.pairing,
    )
    context["actor_id"] = caller.principal.identity
    if spec.scope == "full" and caller.read_only:
        from .accounts.automation_sessions import close_command_session

        close_command_session(caller.portal_session)
        raise PermissionError("This command needs a full-scope session.")
    if spec.changes_state:
        # A fresh gate this command passes names it in the dashboard notice.
        caller.command_type = command_event_type(spec.name)
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


def streaming_command(argv):
    """Whether the command line names a streaming command, parsed or not.

    A usage error is found before any command is chosen, yet a streaming
    command's standard output is the operator's file: its document goes to
    standard error, as every other document of that command does.
    """
    words = list(argv)

    def named(name):
        """Whether ``name``'s words appear together, after any leading options
        (``--config FILE logs export --bad`` is still ``logs export``)."""
        size = len(name)
        return any(words[i : i + size] == name for i in range(len(words)))

    return any(spec.streams and named(spec.name.split()) for spec in COMMANDS)


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
        if args.command_name == "content preview" and (
            (args.delete is None) != (args.expected_version is None)
        ):
            # The Delete is built against a version; the plain-text preview
            # reads no configuration, so it takes none.
            raise UsageError("--expected-version goes with --delete, and only there.")
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
        spec = BY_NAME[args.command_name]
        if (
            spec.prompts
            and not args.yes
            and any(
                value == "-" or (isinstance(value, list) and "-" in value)
                for value in vars(args).values()
            )
        ):
            # Standard input cannot carry both the input and the answer.
            raise UsageError("A prompting command reading - needs --yes.")
    except UsageError as error:
        # The refusal's line comes first; its document names no command,
        # and neither does the line (#1403).
        configure_logging()
        record_command_refusal(
            None, error, {}, admitted=False, changed=False, committed=False
        )
        output = {
            "schema": SCHEMA,
            "command": None,
            "correlation_id": None,
            "ok": False,
            "final": True,
            "session": None,
            "error": {"code": "usage", "message": MESSAGES["usage"]},
        }
        documents = (
            stderr
            if streaming_command(sys.argv[1:] if argv is None else argv)
            else stdout
        )
        print(json.dumps(output, sort_keys=True), file=documents, flush=True)
        return 2
    configure_logging()
    return run(args, stdin=stdin, stdout=stdout, stderr=stderr)
