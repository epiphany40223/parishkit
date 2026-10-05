"""Admin automation sessions: pairing, approval, command sessions and endings.

The host command line acts as one named Administrator through an automation
session that Administrator approved once in a browser signed in with Google
within five minutes (see the Admin automation specification, "Automation
sessions"). The session secret lives only in an owner-only file on the host;
this module sees it once per command, keeps only its SHA-256 digest, and never
logs, prints or stores it.

- **Pairing** stores a pending request in Valkey under a short user code. The
  approval page reads it, and the Administrator approves it into a durable
  ``AutomationSession`` row. ``login wait`` notices the row by its digest,
  consumes the Valkey request and prints the session document.
- **Command sessions**: each command opens an ordinary Admin session for the
  principal, linked to the automation session and carrying an automation
  marker, so every existing session-bound check and SQL guard applies. The
  web refuses a session carrying the marker (``refuse_web_session``).
- **Endings** are recorded once, with a reason, an audit event and a
  dashboard notice. Liveness is read through ``stewardship_automation_live_v1``,
  the same definition the SQL guards use.
- **Notices** go to every Administrator's dashboard; approvals and refused uses
  also open fixed-text operational incidents, which the existing alert routes
  send by email and, when configured, Slack.

There are no rate limits (Administrator decision 12): an unknown secret is
refused at once, and only its notice is grouped, one per host digest per hour.
"""

import hmac
import json
import logging
import re
import secrets
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import (
    BooleanField,
    Exists,
    F,
    Func,
    OuterRef,
    Q,
    Subquery,
    Value,
)
from django.db.models.functions import Greatest
from redis.exceptions import RedisError

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import (
    Event,
    FailureKind,
    current_correlation,
    emit,
)

from .automation_models import (
    SCOPES,
    AutomationLogin,
    AutomationNotice,
    AutomationNoticeAcknowledgement,
    AutomationSession,
)

# Re-exported: the command line needs these before Django is set up.
from .automation_tokens import (  # noqa: F401
    DIGEST_PATTERN,
    SECRET_PATTERN,
    SessionUnusable,
    command_event_type,
    secret_digest,
    valid_digest,
)
from .models import PortalSession
from .policy_models import AdminRevocation, PortalUser
from .policy_schema import normalized_email

PAIRING_SECONDS = 600
MAX_DAYS = 30
# A command session outlives the longest watch (three hours) with a margin,
# and never the automation session itself.
COMMAND_LIFETIME = timedelta(hours=4)
WARNING_WINDOW = timedelta(hours=72)
LISTING_WINDOW = timedelta(days=30)
QUIET_PERIOD = timedelta(hours=1)
REFUSAL_GROUP_SECONDS = 3600
NAMESPACE = "stewardship:auth:v1"

# Django session keys of a command session. The marker holds the automation
# session's UUID; the web refuses any session that carries it.
MARKER = "automation_session"
SCOPE_KEY = "automation_scope"

NAME_PATTERN = re.compile(r"[a-z0-9-]{1,32}")
# Eight letters and digits without the look-alikes 0/O and 1/I: 32 symbols,
# so 40 bits per code.
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 8
# What the command line accepts, and what the row stores.
SCOPE_OPTIONS = {"read-only": "read_only", "full": "full"}

# Read and delete both pairing keys in one step, and only when the digest
# still names this code. The web Valkey ACL grants EVAL, GET and DEL.
CONSUME = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[2])
return 1
"""


class PairingRefused(PermissionError):
    """The approval page refuses a pairing without saying anything about it."""


def valid_label(value):
    """Whether ``value`` is 1 to 64 printable characters, not only spaces."""
    return (
        type(value) is str
        and 1 <= len(value) <= 64
        and value.isprintable()
        and bool(value.strip())
    )


def valid_name(value):
    """Whether ``value`` is a session file name: 1 to 32 of a-z, 0-9 and '-'."""
    return type(value) is str and NAME_PATTERN.fullmatch(value) is not None


def normalized_code(value):
    """The canonical user code for what an Administrator typed, or None.

    Case, spaces and hyphens are ignored, so ``abcd-efgh`` matches ``ABCDEFGH``.
    """
    if type(value) is not str:
        return None
    code = re.sub(r"[\s-]", "", value).upper()
    if len(code) != CODE_LENGTH or set(code) - set(CODE_ALPHABET):
        return None
    return code


def display_code(code):
    """Show a user code in two groups of four, as the command line prints it."""
    return f"{code[:4]}-{code[4:]}"


def database_now():
    """The database clock, the authority for every deadline here."""
    from .sessions import database_now as now

    return now()


@dataclass(frozen=True)
class PairingStore:
    """Pending pairings in the Valkey namespace the web ACL already grants.

    The code key holds the request as JSON; the digest key maps the session
    secret's digest to the code, so ``login wait`` can find its own request.
    Both expire after ten minutes. Nothing here holds the secret itself.
    """

    client: object
    namespace: str = NAMESPACE

    def _code_key(self, code):
        """The key holding one pending request."""
        return f"{self.namespace}:automation-pairing:{code}"

    def _digest_key(self, digest):
        """The key mapping a secret digest to its pending request's code."""
        return f"{self.namespace}:automation-pairing-digest:{digest}"

    def start(self, request):
        """Store ``request`` under a fresh user code and return the code.

        Both keys are written with ``SET NX``: a colliding code draws a new
        one, and a digest that is already pairing is refused.
        """
        value = json.dumps(request, sort_keys=True, separators=(",", ":"))
        for _ in range(16):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if not self.client.set(
                self._code_key(code), value, ex=PAIRING_SECONDS, nx=True
            ):
                continue
            if not self.client.set(
                self._digest_key(request["secret_digest"]),
                code,
                ex=PAIRING_SECONDS,
                nx=True,
            ):
                self.client.delete(self._code_key(code))
                raise ConfigError("This session secret is already being paired.")
            return code
        raise ConfigError("No pairing code could be stored.")

    def request(self, code):
        """The pending request for ``code``, or None when there is none."""
        raw = self.client.get(self._code_key(code))
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            return None
        return value if valid_request(value) else None

    def code_for(self, digest):
        """The user code pending for this secret digest, or None."""
        raw = self.client.get(self._digest_key(digest))
        if raw is None:
            return None
        code = raw.decode("ascii") if isinstance(raw, bytes) else raw
        return normalized_code(code)

    def consume(self, code, digest):
        """Delete both keys atomically; whether this call consumed them."""
        return bool(
            self.client.eval(
                CONSUME, 2, self._code_key(code), self._digest_key(digest), code
            )
        )

    def first_refusal(self, host_digest):
        """Whether this is the first unknown secret from this host in an hour.

        This only groups the notices; it never delays or refuses anything. If
        Valkey cannot answer, the refusal is treated as the first, so a notice
        is never lost to an outage.
        """
        try:
            return bool(
                self.client.set(
                    f"{self.namespace}:automation-refused:{host_digest}",
                    "1",
                    ex=REFUSAL_GROUP_SECONDS,
                    nx=True,
                )
            )
        except RedisError:
            return True


def pairing_request(*, digest, host_digest, name, label, expect_email, scope, days):
    """Validate the operator's pairing options into the stored request.

    ``scope`` is the command line's ``read-only`` or ``full``; ``days`` is 1 to
    30. Raises ValueError naming no value, since the values are the operator's.
    """
    if not valid_digest(digest) or not valid_digest(host_digest):
        raise ValueError("The session secret or host digest is malformed.")
    if not valid_name(name):
        raise ValueError("A session name is 1 to 32 of a-z, 0-9 and '-'.")
    if not valid_label(label):
        raise ValueError("A label is 1 to 64 printable characters.")
    if scope not in SCOPE_OPTIONS:
        raise ValueError("The scope is read-only or full.")
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError("The lifetime is 1 to 30 days.")
    try:
        email = normalized_email(expect_email)
    except ConfigError:
        raise ValueError("The expected email is not a valid address.") from None
    return {
        "secret_digest": digest,
        "host_digest": host_digest,
        "name": name,
        "label": label,
        "expect_email": email,
        "scope": SCOPE_OPTIONS[scope],
        "days": days,
    }


def valid_request(value):
    """Whether a stored pairing request has exactly the shape ``start`` wrote."""
    return (
        type(value) is dict
        and set(value)
        == {
            "secret_digest",
            "host_digest",
            "name",
            "label",
            "expect_email",
            "scope",
            "days",
        }
        and valid_digest(value["secret_digest"])
        and valid_digest(value["host_digest"])
        and valid_name(value["name"])
        and valid_label(value["label"])
        and type(value["expect_email"]) is str
        and value["scope"] in SCOPES
        and type(value["days"]) is int
        and 1 <= value["days"] <= MAX_DAYS
    )


def observe(kind):
    """Open or repeat one automation incident episode, never undoing the action.

    The incident is notification intent in the same transaction; the alert
    routes deliver it after commit. A failure here is logged and leaves the
    dashboard notice, which is the detective control that always exists.
    """
    from parishkit.stewardship.jobs.operational_content import IncidentLevel
    from parishkit.stewardship.jobs.operational_sources import configured_policy
    from parishkit.stewardship.jobs.operational_storage import record_observation

    try:
        with transaction.atomic():
            record_observation(kind, IncidentLevel.CRITICAL, policy=configured_policy())
    except DatabaseError:
        emit(
            Event.STARTUP_REJECTED,
            level=logging.ERROR,
            failure_kind=FailureKind.DATABASE_REFUSED,
        )


def notify(kind, session=None, *, command_type=None, campaign_id=None, actor_id=None):
    """Record one dashboard notice for every Administrator."""
    return AutomationNotice.objects.create(
        kind=kind,
        automation_session=session,
        command_type=command_type,
        campaign_id=campaign_id,
        actor_id=actor_id,
    )


def approve(principal, portal_session, request, *, scope, days):
    """Approve one pending pairing as the signed-in Administrator.

    Runs in the caller's transaction, after the page re-admitted the
    Administrator with a fresh Google sign-in. The approver may lower the
    requested scope and lifetime, never raise them, and must be the address
    the operator expected. The SQL guard re-checks the browser session, its
    five-minute sign-in and the Administrator role.
    """
    from parishkit.stewardship.jobs.operational_content import IncidentKind

    if not connection.in_atomic_block:
        raise RuntimeError("Approval requires its owning transaction.")
    if "administrator" not in principal.roles or not valid_request(request):
        raise PairingRefused("Approval is unavailable.")
    email = PortalUser.objects.values_list("email", flat=True).get(
        pk=principal.identity
    )
    if normalized_email(email) != request["expect_email"]:
        raise PairingRefused("Approval is unavailable.")
    if scope not in SCOPES or (request["scope"] == "read_only" and scope == "full"):
        raise ValueError("The scope can be lowered, not raised.")
    if type(days) is not int or not 1 <= days <= request["days"]:
        raise ValueError("The lifetime can be shortened, not lengthened.")
    now = database_now()
    try:
        with transaction.atomic():
            session = AutomationSession.objects.create(
                principal_id=principal.identity,
                approving_session_id=portal_session.pk,
                authenticated_at=portal_session.authenticated_at,
                expires_at=now + timedelta(days=days),
                scope=scope,
                label=request["label"],
                secret_digest=request["secret_digest"],
                host_digest=request["host_digest"],
                actor_id=principal.identity,
            )
    except IntegrityError:
        # The digest is unique: this pairing was already approved.
        raise PairingRefused("Approval is unavailable.") from None
    record_action(
        Action.AUTOMATION_SESSION_APPROVED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=principal.identity,
        subject_id=session.pk,
        context={"outcome": Outcome.SUCCEEDED},
    )
    notify("approved", session, actor_id=principal.identity)
    observe(IncidentKind.AUTOMATION_APPROVED)
    return session


def is_live(session_id):
    """Whether the session is live now, by the SQL guards' own definition."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.stewardship_automation_live_v1(%s)", [session_id])
        return cursor.fetchone()[0] is True


def lapse_reason(session):
    """Why an unrevoked, unexpired session is no longer live.

    A recovery after approval wins, then a disabled or removed user;
    otherwise the principal's current rule no longer grants Administrator.
    """
    if AdminRevocation.objects.filter(created_at__gt=session.created_at).exists():
        return "recovery"
    if not PortalUser.objects.filter(pk=session.principal_id, disabled=False).exists():
        return "user_removed"
    return "role_lost"


def end_session(session, *, reason, actor_id=None):
    """End one unrevoked, unexpired session once, with its reason, audit
    event and notice.

    Runs in the caller's transaction and locks the row first. Returns whether
    this call ended it. A session already revoked, or past its deadline, is
    left as it is: an expiry is an ending of its own, with no revoked stamp
    and no notice. A session that is unexpired but no longer live (its
    Administrator lost the role, for example) is ended with that lapse
    reason. ``actor_id`` is the revoking Administrator, or None when the
    system ended it.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Ending a session requires its owning transaction.")
    row = AutomationSession.objects.select_for_update().get(pk=session.pk)
    now = database_now()
    if row.revoked_at is not None or row.expires_at <= now:
        return False
    AutomationSession.objects.filter(pk=row.pk).update(
        revoked_at=max(now, row.created_at),
        end_reason=reason,
        version=F("version") + 1,
        actor_id=actor_id,
        correlation_id=current_correlation(),
    )
    record_action(
        Action.AUTOMATION_SESSION_ENDED,
        actor_kind=ActorKind.PORTAL_USER if actor_id else ActorKind.SYSTEM,
        actor_id=actor_id,
        subject_id=row.pk,
        context={"outcome": Outcome.SUCCEEDED},
    )
    notify("ended", row, actor_id=actor_id)
    return True


def record_refusal(session=None):
    """Notice, audit and alert one refused use (subject: the session, if any)."""
    from parishkit.stewardship.jobs.operational_content import IncidentKind

    record_action(
        Action.AUTOMATION_SESSION_REFUSED,
        actor_kind=ActorKind.SYSTEM,
        subject_id=None if session is None else session.pk,
        context={"outcome": Outcome.DENIED},
    )
    notify("refused", session)
    observe(IncidentKind.AUTOMATION_REFUSED)
    log_refusal()


def log_refusal():
    """Log one refused command; every refusal is logged, grouped or not."""
    emit(
        Event.STARTUP_REJECTED,
        level=logging.WARNING,
        failure_kind=FailureKind.AUTOMATION_REFUSED,
    )


def open_command_session(secret, host_digest, *, store, pairing=None):
    """Admit one command: find, check and use the automation session.

    Returns ``(session_row, django_session)``: a new Admin session for the
    principal, linked to the automation session, with the automation
    session's sign-in instant, a deadline at most four hours away and never
    after the automation session's, and the automation marker in its data.
    Raises ``SessionUnusable``. A host mismatch revokes the session; a session
    whose Administrator lost the role, was removed or was recovered is ended
    with that reason. Those endings commit before the refusal is raised.
    """
    from .policy import current_principal
    from .sessions import create_admin_session

    try:
        digest = secret_digest(secret)
    except ValueError:
        digest = None
    if not valid_digest(host_digest):
        # A malformed host digest names no host to group a notice by, so it
        # is only logged, like every other refusal, and refused at once.
        log_refusal()
        raise SessionUnusable("session_missing")
    outcome = session = None
    with transaction.atomic():
        row = (
            AutomationSession.objects.select_for_update()
            .filter(secret_digest=digest)
            .first()
            if digest
            else None
        )
        now = database_now()
        principal = None
        if row is not None and row.revoked_at is None and row.expires_at > now:
            if not hmac.compare_digest(row.host_digest, host_digest):
                end_session(row, reason="host_mismatch")
                record_refusal(row)
            elif not is_live(row.pk):
                end_session(row, reason=lapse_reason(row))
            else:
                principal = current_principal(store, row.principal_id)
                if "administrator" not in principal.roles:
                    end_session(row, reason="role_lost")
                    principal = None
        if principal is None:
            outcome = "session_missing" if row is None else "session_ended"
        else:
            session, portal = create_admin_session(
                principal,
                now=now,
                authenticated_at=row.authenticated_at,
                expires_at=min(now + COMMAND_LIFETIME, row.expires_at),
                data={MARKER: str(row.pk), SCOPE_KEY: row.scope},
            )
            AutomationLogin.objects.create(
                portal_session_id=portal.pk, automation_session=row
            )
            AutomationSession.objects.filter(pk=row.pk).update(
                last_used_at=now,
                version=F("version") + 1,
                correlation_id=current_correlation(),
            )
            row.last_used_at = now
    # Every unknown secret is logged and refused at once; only its notice,
    # audit event and alert are grouped, once per host digest per hour.
    if outcome == "session_missing":
        if pairing is None or pairing.first_refusal(host_digest):
            with transaction.atomic():
                record_refusal(None)
        else:
            log_refusal()
    if outcome is not None:
        raise SessionUnusable(outcome)
    return row, session


def close_command_session(portal_session):
    """End a command session at exit, without an audit event.

    The automation session's events carry the attribution; a crashed process
    leaves a row that idles out and is cleaned up as usual.
    """
    if portal_session is None:
        return
    with transaction.atomic():
        now = database_now()
        PortalSession.objects.filter(
            pk=portal_session.pk, revoked_at__isnull=True
        ).update(
            revoked_at=Greatest(
                Value(now), F("last_activity_at"), F("authenticated_at")
            ),
            version=F("version") + 1,
        )


def heartbeat(portal_session):
    """Record activity on a command session during a long ``--watch``.

    The 60-minute idle check of ordinary admission then holds while the
    process lives (see the specification's "Command sessions"); the caller
    beats at most once a minute. Only a command session that is still live
    (not revoked, not expired and not already idle past the limit) is
    renewed, so a beat never revives one that has ended. It is not the
    activity a state-changing page action records, so a read-only session's
    watch beats too. Returns whether the session was renewed.
    """
    from .session_policy import ADMIN_IDLE

    with transaction.atomic():
        now = database_now()
        return bool(
            PortalSession.objects.filter(
                pk=portal_session.pk,
                revoked_at__isnull=True,
                expires_at__gt=now,
                last_activity_at__gt=now - ADMIN_IDLE,
            ).update(last_activity_at=now, version=F("version") + 1)
        )


def refuse_web_session(session_store):
    """Refuse a web request that carries a command session's key.

    A command session's key never leaves its command process, so such a
    request means tampering inside the container. Its Admin session is
    revoked, its automation session is ended as ``misused``, and the refusal
    is noticed, audited and alerted.
    """
    from .sessions import _revoke

    try:
        automation_id = UUID(str(session_store.get(MARKER)))
    except (TypeError, ValueError):
        automation_id = None
    with transaction.atomic():
        row = (
            PortalSession.objects.select_for_update()
            .filter(session_id=session_store.session_key)
            .first()
        )
        if row is not None:
            _revoke(row, database_now(), "admin_revoked")
        session = (
            AutomationSession.objects.filter(pk=automation_id).first()
            if automation_id
            else None
        )
        if session is not None:
            end_session(session, reason="misused")
        record_refusal(session)


def revoke_all_offline(reason, *, correlation_id):
    """End every unrevoked, unexpired session at once, as the admin-recovery login.

    One transaction: the ending, then one ``automation_session_ended`` audit
    event and one ``ended`` notice per session, written with plain INSERTs
    because that login can insert but not read them. Returns the count.
    """
    if reason not in {"restore", "revoked_by_operator"}:
        raise ValueError("Unknown offline revocation.")
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE public.stewardship_automation_session "
            "SET revoked_at=statement_timestamp(), end_reason=%s, "
            "version=version+1, actor_id=NULL, correlation_id=%s "
            "WHERE revoked_at IS NULL AND expires_at>statement_timestamp() "
            "RETURNING id",
            [reason, correlation_id],
        )
        identifiers = [row[0] for row in cursor.fetchall()]
        if identifiers:
            cursor.execute(
                "INSERT INTO public.stewardship_audit_event "
                "(id, actor_id, correlation_id, event_type, subject_id) "
                "SELECT gen_random_uuid(), NULL, %s, %s, unnest(%s::uuid[])",
                [
                    correlation_id,
                    Action.AUTOMATION_SESSION_ENDED.value,
                    identifiers,
                ],
            )
            cursor.execute(
                "INSERT INTO public.stewardship_automation_notice "
                "(id, actor_id, correlation_id, kind, automation_session_id) "
                "SELECT gen_random_uuid(), NULL, %s, 'ended', unnest(%s::uuid[])",
                [correlation_id, identifiers],
            )
    return len(identifiers)


def live_annotation():
    """The SQL liveness of each row, for listings that show it at read time."""
    return Func(
        F("id"),
        function="public.stewardship_automation_live_v1",
        output_field=BooleanField(),
    )


def sessions_of(principal_id, now):
    """This Administrator's sessions: live, and ended within the last 30 days.

    One query, newest first, with liveness computed now. Never a secret or
    digest; the host digest is shortened for display by ``session_row``.
    """
    since = now - LISTING_WINDOW
    rows = (
        AutomationSession.objects.filter(principal_id=principal_id)
        .filter(
            Q(revoked_at__isnull=True, expires_at__gt=since) | Q(revoked_at__gte=since)
        )
        .annotate(live=live_annotation())
        .order_by("-created_at", "-id")
    )
    return [session_row(row) for row in rows]


def live_sessions():
    """Every live session, with its Administrator's address, newest first."""
    email = PortalUser.objects.filter(pk=OuterRef("principal_id")).values("email")[:1]
    rows = (
        AutomationSession.objects.filter(revoked_at__isnull=True)
        .annotate(live=live_annotation(), principal_email=Subquery(email))
        .filter(live=True)
        .order_by("-created_at", "-id")
    )
    return [session_row(row) for row in rows]


def session_row(row):
    """A session for a page or document: no secret, and no full digest."""
    return {
        "id": row.pk,
        "label": row.label,
        "scope": row.scope,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "end_reason": row.end_reason,
        "host": row.host_digest[:12],
        "live": getattr(row, "live", None),
        "principal_id": row.principal_id,
        "principal_email": getattr(row, "principal_email", None),
        "version": row.version,
    }


def revoke(session_id, actor, *, reason):
    """An Administrator revokes a live session from the portal.

    ``reason`` is ``revoked_by_owner`` or ``revoked_by_administrator``; the
    caller picks it by whether the session is the actor's own. Only
    ``revoked_by_owner`` is checked here: it must name the actor's own
    session. Only a live session is revoked:
    one that has expired or lapsed is left to its own ending (lapses are
    recorded by the hourly maintenance or the next command), so it gets no
    revoked stamp and no notice. Returns whether this call ended it.
    """
    if reason not in {"revoked_by_owner", "revoked_by_administrator"}:
        raise ValueError("Unknown revocation.")
    with transaction.atomic():
        # Lock the row before the liveness check, so a lapse recorded
        # concurrently (role_lost, for example) either commits first and
        # is seen here, or waits and then finds the session already ended.
        session = (
            AutomationSession.objects.select_for_update().filter(pk=session_id).first()
        )
        if session is None or (
            reason == "revoked_by_owner" and session.principal_id != actor.identity
        ):
            raise LookupError("Automation session is unavailable.")
        if not is_live(session.pk):
            return False
        return end_session(session, reason=reason, actor_id=actor.identity)


def open_notices(viewer, *, limit=10):
    """The notices this Administrator has not acknowledged, newest first.

    Two queries: the count and the newest ``limit``. Labels are returned as
    stored; the template escapes them as text.
    """
    pending = AutomationNotice.objects.exclude(
        Exists(
            AutomationNoticeAcknowledgement.objects.filter(
                notice=OuterRef("pk"), administrator_id=viewer.identity
            )
        )
    )
    total = pending.count()
    if not total:
        return {"total": 0, "rows": []}
    rows = [
        {
            "id": notice.pk,
            "kind": notice.kind,
            "label": None
            if notice.automation_session is None
            else notice.automation_session.label,
            "command_type": notice.command_type,
            "created_at": notice.created_at,
        }
        for notice in pending.select_related("automation_session").order_by(
            "-created_at", "-id"
        )[:limit]
    ]
    return {"total": total, "rows": rows}


def acknowledge_notices(viewer, notice_ids=None):
    """Acknowledge notices for this Administrator's own dashboard only.

    ``notice_ids`` None acknowledges every notice still shown to them.
    Returns how many acknowledgements were recorded, with one audit event.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("Acknowledgement requires its owning transaction.")
    pending = AutomationNotice.objects.exclude(
        Exists(
            AutomationNoticeAcknowledgement.objects.filter(
                notice=OuterRef("pk"), administrator_id=viewer.identity
            )
        )
    )
    if notice_ids is not None:
        pending = pending.filter(pk__in=notice_ids)
    identifiers = list(pending.values_list("pk", flat=True)[:1000])
    AutomationNoticeAcknowledgement.objects.bulk_create(
        [
            AutomationNoticeAcknowledgement(
                notice_id=identifier,
                administrator_id=viewer.identity,
                actor_id=viewer.identity,
            )
            for identifier in identifiers
        ],
        ignore_conflicts=True,
    )
    if identifiers:
        record_action(
            Action.AUTOMATION_NOTICES_ACKNOWLEDGED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=viewer.identity,
            subject_id=identifiers[0] if len(identifiers) == 1 else None,
            context={"count": len(identifiers), "outcome": Outcome.SUCCEEDED},
        )
    return len(identifiers)


def delete_orphaned_logins(*, limit=1000):
    """Delete command logins whose Admin session cleanup already removed."""
    with transaction.atomic():
        identifiers = list(
            AutomationLogin.objects.filter(
                ~Exists(PortalSession.objects.filter(pk=OuterRef("portal_session_id")))
            )
            .order_by("created_at")
            .values_list("pk", flat=True)[:limit]
        )
        AutomationLogin.objects.filter(pk__in=identifiers).delete()
        return len(identifiers)


def end_lapsed_sessions(*, limit=100):
    """Record the endings that role loss, removal and recovery already imply.

    Listings and guards already show these sessions as ended; this writes the
    reason on the row, with its audit event and notice. At most ``limit`` per
    pass; the next hourly pass takes the rest.
    """
    candidates = list(
        AutomationSession.objects.filter(
            revoked_at__isnull=True, expires_at__gt=database_now()
        )
        .annotate(live=live_annotation())
        .filter(live=False)
        .order_by("created_at")[:limit]
    )
    ended = 0
    for session in candidates:
        with transaction.atomic():
            row = AutomationSession.objects.select_for_update().get(pk=session.pk)
            if row.revoked_at is None and not is_live(row.pk):
                ended += end_session(row, reason=lapse_reason(row))
    return ended


def resolve_quiet_incidents():
    """Resolve each open automation episode that has had no event for an hour.

    The routes then send their resolved notice, and the next event opens a
    new episode with a fresh message.
    """
    from parishkit.stewardship.jobs.operational_content import AUTOMATION_KINDS
    from parishkit.stewardship.jobs.operational_storage import record_recovery

    quiet_since = database_now() - QUIET_PERIOD
    resolved = 0
    for kind in AUTOMATION_KINDS:
        row = record_recovery(kind, healthy_since=quiet_since)
        resolved += row is not None and row.resolved_at is not None
    return resolved


def maintain(*, effect=nullcontext, check=lambda: None, batch_size=500, batches=20):
    """One maintenance pass: session cleanup, endings and quiet incidents.

    Each step runs inside ``effect()`` (the task's fenced transaction; nothing
    in tests). ``cleanup_admin_sessions`` runs in bounded batches until one
    comes back short or ``batches`` ran, then the orphaned command logins go.
    ``check`` is called before each step, so a lost task lease stops the pass.
    """
    from .sessions import cleanup_admin_sessions

    removed = 0
    for _ in range(batches):
        check()
        with effect():
            count = cleanup_admin_sessions(batch_size=batch_size)
        removed += count
        if count < batch_size:
            break
    totals = {"admin_sessions_removed": removed}
    for name, step in (
        ("command_logins_removed", delete_orphaned_logins),
        ("sessions_ended", end_lapsed_sessions),
        ("incidents_resolved", resolve_quiet_incidents),
    ):
        check()
        with effect():
            totals[name] = step()
    return totals
