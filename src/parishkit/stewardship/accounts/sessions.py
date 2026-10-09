"""Separate PostgreSQL cookie namespaces and live policy-based Admin sessions."""

import hashlib
import json
from importlib import import_module

from django.conf import global_settings, settings
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.models import Session
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Greatest
from django.middleware.csrf import (
    CSRF_ALLOWED_CHARS,
    CSRF_SECRET_LENGTH,
    CsrfViewMiddleware,
    InvalidTokenFormat,
)
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.web.namespaces import cookie_namespace

from .admin_caller import AUTOMATION, WEB, as_caller
from .models import AdminRevocation, PortalSession, PortalUser
from .policy import current_principal
from .session_policy import (
    ADMIN_ABSOLUTE as ADMIN_ABSOLUTE,
)
from .session_policy import (
    ADMIN_IDLE as ADMIN_IDLE,
)
from .session_policy import (
    FAMILY_ABSOLUTE as FAMILY_ABSOLUTE,
)
from .session_policy import (
    FAMILY_IDLE as FAMILY_IDLE,
)


class NamespacedSessionMiddleware:
    """Only cookie transport is shared; no endpoint imports the other namespace."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.store = import_module(settings.SESSION_ENGINE).SessionStore

    def __call__(self, request):
        """Django save/expiry semantics without per-request global setting changes."""
        name, _, path = cookie_namespace(request)
        request.session = self.store(request.COOKIES.get(name))
        response = self.get_response(request)
        session = request.session
        if session.accessed:
            patch_vary_headers(response, ("Cookie",))
        if session.is_empty():
            if name in request.COOKIES:
                response.delete_cookie(path=path, key=name, samesite="Lax")
        elif (
            session.modified or getattr(session, "stewardship_persisted", False)
        ) and response.status_code < 500:
            age = session.get_expiry_age()
            try:
                if session.modified:
                    session.save()
            except import_module(settings.SESSION_ENGINE).UpdateError:
                raise SessionInterrupted(
                    "Session ended; please log in again."
                ) from None
            response.set_cookie(
                name,
                session.session_key,
                max_age=age,
                expires=http_date(timezone.now().timestamp() + age),
                path=path,
                secure=settings.SESSION_COOKIE_SECURE,
                httponly=True,
                samesite="Lax",
            )
        return response


class NamespacedCsrfMiddleware(CsrfViewMiddleware):
    """Django CSRF checks with one secret cookie per session namespace.

    Django reads a single global CSRF_COOKIE_NAME, so a Family login's
    rotate_token() used to invalidate tokens rendered in open Admin pages in
    the same browser, and vice versa. Only the two cookie hooks change; token
    masking, comparison, origin/referer checks and rotation are Django's.
    Rotation on login or privilege change still replaces the secret, now only
    within its own namespace. CSRF_USE_SESSIONS is not used because it would
    persist a database session for every anonymous sign-in page view.

    The namespace supplies the cookie name and path; CSRF_COOKIE_DOMAIN,
    AGE, SECURE, HTTPONLY and SAMESITE apply as usual. Settings this class
    would silently ignore refuse to load instead.
    """

    def __init__(self, get_response):
        """Refuse CSRF settings that the namespaced cookies cannot honor."""
        if (
            settings.CSRF_USE_SESSIONS
            or settings.CSRF_COOKIE_NAME != global_settings.CSRF_COOKIE_NAME
            or settings.CSRF_COOKIE_PATH != global_settings.CSRF_COOKIE_PATH
        ):
            raise ImproperlyConfigured(
                "Namespaced CSRF cookies set their own name and path and "
                "do not support CSRF_USE_SESSIONS."
            )
        super().__init__(get_response)

    def _get_secret(self, request):
        """Read the namespace secret; a malformed value is replaced, not trusted."""
        secret = request.COOKIES.get(cookie_namespace(request)[1])
        if secret is None:
            return None
        # These cookies never held Django's pre-4.0 masked form, so only an
        # unmasked secret is well formed.
        if len(secret) != CSRF_SECRET_LENGTH or set(secret) - set(CSRF_ALLOWED_CHARS):
            raise InvalidTokenFormat("malformed")
        return secret

    def _set_csrf_cookie(self, request, response):
        """Write the secret with the global CSRF cookie attributes, per namespace."""
        _, name, path = cookie_namespace(request)
        response.set_cookie(
            name,
            request.META["CSRF_COOKIE"],
            max_age=settings.CSRF_COOKIE_AGE,
            domain=settings.CSRF_COOKIE_DOMAIN,
            path=path,
            secure=settings.CSRF_COOKIE_SECURE,
            httponly=settings.CSRF_COOKIE_HTTPONLY,
            samesite=settings.CSRF_COOKIE_SAMESITE,
        )
        patch_vary_headers(response, ("Cookie",))


def database_now():
    """Use the authoritative database clock for session admission and expiry."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


def revocation_epoch():
    """Offline recovery invalidates earlier OAuth states and session metadata."""
    latest = AdminRevocation.objects.order_by("-created_at", "-id").first()
    return str(latest.pk) if latest else "initial"


def _authority_fingerprint(principal):
    """Detect privilege transitions without treating stored scopes as authority."""
    value = json.dumps(
        [sorted(principal.roles), sorted(principal.ministries)],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(value).hexdigest()


def _rotate_authority(caller, row, principal, now):
    """Replace a locked session without extending Google freshness or lifetime.

    Django's cycle_key deletes its protected parent, so create a new parent and
    metadata explicitly. Revoked metadata stays available for ordered cleanup.
    Concurrent requests holding the old cookie see only the revoked row. Only
    a web caller rotates; the caller also rotates the request's CSRF token.
    """
    _revoke(row, now, "admin_privileges_changed")
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session["principal"] = str(row.principal_id)
    session["recovery_epoch"] = caller.session.get("recovery_epoch")
    session["authority_fingerprint"] = _authority_fingerprint(principal)
    session.set_expiry(row.expires_at)
    session.save()
    # The response may next acquire a read-only guard. Transport still needs a
    # Set-Cookie, but must not repeat this already committed parent-row write.
    session.modified = False
    session.stewardship_persisted = True
    replacement = PortalSession.objects.create(
        session_id=session.session_key,
        principal_id=row.principal_id,
        authenticated_at=row.authenticated_at,
        last_activity_at=row.last_activity_at,
        expires_at=row.expires_at,
        actor_id=row.principal_id,
    )
    caller.replace_session(session)
    return replacement


def _revoke(row, now, reason):
    """One locked revocation creates exactly one safe permanent audit envelope."""
    if row.revoked_at is None:
        PortalSession.objects.filter(pk=row.pk).update(
            revoked_at=max(now, row.last_activity_at, row.authenticated_at),
            version=F("version") + 1,
        )
        AuditEvent.objects.create(
            event_type=reason,
            actor_id=row.principal_id,
            subject_id=row.pk,
        )


def end_admin(request, *, reason="admin_logout"):
    """Invalidate authority immediately; ordered cleanup later removes the parent."""
    if reason not in {
        "admin_logout",
        "admin_reauthenticated",
        "admin_revoked",
        "admin_timeout",
    }:
        raise ValueError("Unknown session-ending reason.")
    with transaction.atomic():
        row = (
            PortalSession.objects.select_for_update()
            .filter(session_id=request.session.session_key)
            .first()
        )
        if row:
            _revoke(row, database_now(), reason)
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()


def create_admin_session(
    principal, *, now, authenticated_at, expires_at, session=None, data=None
):
    """Store one new Admin session and its PortalSession row: the shared core.

    ``issue_admin`` passes the request's session store after a verified
    sign-in; an Admin automation command passes none (a new store is made)
    and adds its automation marker through ``data``. Neither ends another
    session here, and neither records an audit event here. Returns the
    session store and the row.
    """
    if session is None:
        session = import_module(settings.SESSION_ENGINE).SessionStore()
    session["principal"] = str(principal.identity)
    session["recovery_epoch"] = revocation_epoch()
    session["authority_fingerprint"] = _authority_fingerprint(principal)
    for key, value in (data or {}).items():
        session[key] = value
    session.set_expiry(expires_at)
    session.save()
    row = PortalSession.objects.create(
        session_id=session.session_key,
        principal_id=principal.identity,
        authenticated_at=authenticated_at,
        last_activity_at=now,
        expires_at=expires_at,
        actor_id=principal.identity,
    )
    return session, row


def issue_admin(request, user_id, *, store, authenticated_at):
    """Issue only after verified identity and fresh current-policy authorization."""
    principal = current_principal(store, user_id)
    if not principal.roles:
        raise PermissionError("Administration access is unavailable.")
    end_admin(request, reason="admin_reauthenticated")
    with transaction.atomic():
        now = database_now()
        if timezone.is_naive(authenticated_at) or authenticated_at > now:
            raise PermissionError("Verified Google authentication is required.")
        # Ordinary Google SSO may predate this application session. Its signed
        # time gates privileged actions, not the new session's absolute limit.
        _, row = create_admin_session(
            principal,
            now=now,
            authenticated_at=authenticated_at,
            expires_at=now + ADMIN_ABSOLUTE,
            session=request.session,
        )
        AuditEvent.objects.create(
            event_type="admin_login", actor_id=user_id, subject_id=row.pk
        )
    return principal


def reauthenticate_admin(request, user_id, *, store, authenticated_at):
    """Step-up: refresh the current live session's Google freshness in place.

    This applies only when the request already carries a live, currently
    authorized Admin session whose principal is the PortalUser that just
    completed a verified Google sign-in. That row's ``authenticated_at``
    advances to the verified instant (never backwards), activity renews, and
    the Django session key and PortalSession row stay the same, so work bound
    to this login, such as the initial setup draft, survives. Returns the
    principal, or None when there is no such session; the caller then issues
    a new session exactly as for a first login.

    Keeping the key is not a session-fixation risk. Every initial login
    already replaced the pre-login key with a fresh server-issued one bound
    to this principal, and step-up changes neither the principal nor its
    authority, which ``authenticated_admin`` re-derives from current policy.
    It only records that the same person proved presence with Google again.
    A different Google account never reaches this row: it gets a new session.
    The caller still rotates the CSRF secret, as it does after any login.
    """
    with transaction.atomic():
        # Admission locks the row and revokes an idle, expired or recovered
        # session first, so a dead session is replaced rather than revived.
        principal = authenticated_admin(request, store=store)
        if principal is None or principal.identity != user_id:
            return None
        row = request.portal_session
        now = database_now()
        if timezone.is_naive(authenticated_at) or authenticated_at > now:
            raise PermissionError("Verified Google authentication is required.")
        # A signed auth_time may predate the session's current instant (an
        # older Google session); freshness then simply does not advance.
        fresh = max(row.authenticated_at, authenticated_at)
        PortalSession.objects.filter(pk=row.pk).update(
            authenticated_at=fresh,
            last_activity_at=now,
            version=F("version") + 1,
        )
        row.authenticated_at, row.last_activity_at = fresh, now
        AuditEvent.objects.create(
            event_type="admin_step_up", actor_id=user_id, subject_id=row.pk
        )
    return principal


def authenticated_admin(
    caller, *, store, activity=False, read_only=False, stale_authority=False
):
    """Re-evaluate policy every time; passive status/presence calls never renew idle.

    ``stale_authority`` (read-only checks only) admits a session whose roles or
    Ministries changed since it was issued, without rotating it. Only the
    session status endpoint uses it, and it reveals nothing but deadlines: a
    changed role is not a signed-out session (#755). The next ordinary page
    rotates the session as usual.

    ``caller`` is an ``AdminCaller`` (until the final ADM-11 PR, a Django
    request is still converted). A read-only automation session can never
    record activity, and an automation caller is never rotated: a changed
    authority refuses it instead.

    The two channels never mix. A web request whose session carries the
    automation marker (a command session's key, which never leaves its
    command process) is refused and its sessions are revoked
    (``automation_sessions.refuse_web_session``); a read-only check records
    that once its transaction ends. An automation
    caller is admitted only on its own command session, while its automation
    session is live and its principal still an Administrator.
    """
    from .automation_sessions import MARKER

    if activity and read_only:
        raise ValueError("Read-only authorization cannot renew session activity.")
    if stale_authority and not read_only:
        raise ValueError("Only a read-only check may admit stale authority.")
    caller = as_caller(caller)
    if activity and caller.read_only:
        raise PermissionError("Access is unavailable.")
    if caller.channel == WEB and caller.session.get(MARKER) is not None:
        from .automation_sessions import refuse_web_session

        if read_only:
            # A read-only check may run inside a read-only snapshot, which
            # cannot write: the refusal is recorded as soon as that
            # transaction ends (at once outside one).
            store = caller.session
            transaction.on_commit(lambda: refuse_web_session(store))
        else:
            refuse_web_session(caller.session)
        return None
    # A read-only recheck has no writes to recover independently. Reuse an
    # enclosing disclosure/audit transaction without two redundant savepoint
    # statements; a database error still makes that whole response fail closed.
    with transaction.atomic(savepoint=not read_only):
        query = PortalSession.objects.all()
        if not read_only:
            query = query.select_for_update()
        row = query.filter(session_id=caller.session.session_key).first()
        if row is None or row.revoked_at is not None:
            return None
        now = database_now()
        reason = None
        if now >= min(row.expires_at, row.last_activity_at + ADMIN_IDLE):
            reason = "admin_timeout"
        elif caller.session.get("recovery_epoch") != revocation_epoch():
            reason = "admin_revoked"
        try:
            principal = (
                current_principal(store, row.principal_id) if not reason else None
            )
        except PortalUser.DoesNotExist:
            principal = None
        if principal is None or not principal.roles:
            reason = reason or "admin_revoked"
        if reason:
            if not read_only:
                _revoke(row, now, reason)
            return None
        if caller.channel != WEB and not _automation_admitted(caller, principal):
            # The command line maps this to an ended session (exit 5); the
            # command ends its command session at exit.
            return None
        if caller.session.get("authority_fingerprint") != _authority_fingerprint(
            principal
        ):
            # A read guard cannot write or rotate a cookie after headers start.
            # Its ordinary admission must first establish a current session.
            # Only the web rotates; automation re-admits on its next command.
            if stale_authority and caller.channel == WEB:
                caller.admitted(row, principal)
                return principal
            if read_only or caller.channel != WEB:
                return None
            row = _rotate_authority(caller, row, principal, now)
        if activity:
            PortalSession.objects.filter(pk=row.pk).update(
                last_activity_at=now,
                version=F("version") + 1,
            )
            row.last_activity_at = now
        caller.admitted(row, principal)
        return principal


def _automation_admitted(caller, principal):
    """Whether an automation caller may act on its command session now.

    Its session must carry the marker naming its own automation session,
    which must be live (``stewardship_automation_live_v1``, the guards' own
    definition), and its principal must still be an Administrator.
    """
    from .automation_sessions import MARKER, is_live

    return (
        caller.session.get(MARKER) == str(caller.automation_session_id)
        and "administrator" in principal.roles
        and is_live(caller.automation_session_id)
    )


class FreshAuthenticationRequired(PermissionError):
    """The session is valid, but this action needs a Google sign-in within 5 min.

    It stays a PermissionError, so every existing denial path still refuses.
    Admin error handling can instead offer the step-up confirmation page.
    """


FRESH_SECONDS = 300


def freshness(request):
    """Say whether a gated action would be admitted now, for page notices only.

    Returns ``(fresh, minutes)``: ``minutes`` is how long ago this session last
    signed in with Google (``None`` without a live session). It never admits
    anything; every gated command still calls ``require_fresh`` itself.
    """
    row = getattr(request, "portal_session", None)
    if row is None:
        return False, None
    age = (database_now() - row.authenticated_at).total_seconds()
    return 0 <= age <= FRESH_SECONDS, max(0, int(age // 60))


def require_fresh(caller, *, irreversible=False, record=True):
    """A fresh Google round trip, not a browser flag, admits privileged commands.

    ``caller`` is an ``AdminCaller`` (until the final ADM-11 PR, a Django
    request is still converted). A web caller must have signed in with Google
    within the last five minutes. An automation caller stands in for that
    sign-in with a live, full-scope automation session (ADM-11 PR 5; see
    ``_automation_fresh``). Returns the sign-in instant the action records,
    which its SQL guard compares with the session row. ``irreversible`` marks
    the actions whose automation notice is ``irreversible`` (the Production
    confirmation and pre-start withdrawal); the web ignores it. A passive
    probe (a page showing whether a fresh sign-in is in place) passes
    ``record=False``: it is no action, so it records no fresh-gate event or
    notice.
    """
    caller = as_caller(caller)
    if caller.channel == AUTOMATION:
        instant = _automation_fresh(caller)
        if record:
            _record_automation_fresh(caller, irreversible=irreversible)
        return instant
    row = caller.portal_session
    if (
        caller.channel != WEB
        or row is None
        or not 0
        <= (database_now() - row.authenticated_at).total_seconds()
        <= FRESH_SECONDS
    ):
        raise FreshAuthenticationRequired("Please authenticate with Google again.")
    return row.authenticated_at


def _automation_fresh(caller):
    """A live, full-scope automation session stands in for a fresh sign-in.

    The automation session row is locked ``FOR SHARE`` in the caller's
    transaction, so an ending (revocation, logout, role loss) waits for the
    action to commit, and an ending that committed first is seen here. The
    session must be live (``stewardship_automation_live_v1``: unrevoked,
    unexpired, its principal still an Administrator, no later recovery), of
    full scope and the admitted principal's, and the command session must
    carry its sign-in instant. That instant is returned: the action records
    it as the page records a fresh sign-in, and the SQL guards accept it
    through ``stewardship_automation_fresh_v1``. A read-only session never
    passes. It must run in the action's transaction: outside one, the lock
    would end at once and prove nothing about the action's commit.
    """
    row = caller.portal_session
    if caller.read_only or row is None or caller.principal is None:
        raise FreshAuthenticationRequired("This needs a full-scope session.")
    if not connection.in_atomic_block:
        from parishkit.stewardship.storage import StorageInvariantError

        raise StorageInvariantError("An automation fresh gate needs its transaction.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.scope,a.principal_id,a.authenticated_at,"
            "public.stewardship_automation_live_v1(a.id) "
            "FROM public.stewardship_automation_session a WHERE a.id=%s FOR SHARE",
            [caller.automation_session_id],
        )
        found = cursor.fetchone()
    if (
        found is None
        or found[0] != "full"
        or found[1] != caller.principal.identity
        or found[2] != row.authenticated_at
        or found[3] is not True
    ):
        raise FreshAuthenticationRequired("This needs a live full-scope session.")
    return found[2]


def _record_automation_fresh(caller, *, irreversible):
    """Tell Administrators that an automation session passed a fresh gate.

    Once per state-changing command (the command line sets ``command_type``;
    a read or preview has none and records nothing), in the action's own
    transaction so it commits or rolls back with the action. "Once" is per
    invocation and per kind, read from the database: a notice of the same
    kind, session and correlation already recorded suppresses another (an
    attempt that rolled back left none, so a retried attempt records again;
    an irreversible gate after a fresh-gated one still records its own
    notice and incident): an
    ``automation_fresh_gate`` audit event (the Administrator as actor, the
    automation session as subject) distinguishes an action whose fresh
    sign-in an automation session stood in for, and a dashboard notice
    names the session, command and campaign. An irreversible action's
    notice is ``irreversible`` and also observes the
    ``automation_irreversible`` incident, which emails and posts to Slack
    after commit.
    """
    from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
    from parishkit.stewardship.audit.services import record_action
    from parishkit.stewardship.jobs.operational_content import IncidentKind

    from .automation_models import AutomationNotice
    from .automation_sessions import notify, observe

    if caller.command_type is None:
        return
    if (
        caller.correlation_id is not None
        and AutomationNotice.objects.filter(
            automation_session_id=caller.automation_session_id,
            correlation_id=caller.correlation_id,
            kind="irreversible" if irreversible else "fresh_gated",
        ).exists()
    ):
        return
    record_action(
        Action.AUTOMATION_FRESH_GATE,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=caller.principal.identity,
        subject_id=caller.automation_session_id,
        context={"outcome": Outcome.SUCCEEDED},
    )
    notify(
        "irreversible" if irreversible else "fresh_gated",
        caller.automation_session,
        command_type=caller.command_type,
        campaign_id=caller.campaign_id,
        actor_id=caller.principal.identity,
    )
    if irreversible:
        observe(IncidentKind.AUTOMATION_IRREVERSIBLE)


def cleanup_admin_sessions(*, batch_size=500):
    """Delete protected metadata before parents, retaining opaque audit attribution.

    The automation maintenance task runs this as the general worker, which
    may read only the session columns named below and never a session key,
    so the query is limited to them and the Django sessions are deleted by
    ``stewardship_admin_session_purge_v1``, which finds the keys itself and
    deletes only those of ended rows. The rows go next, in the same
    transaction, so the deferred foreign key holds at commit.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Session cleanup requires a bounded batch size.")
    with transaction.atomic():
        now = database_now()
        rows = list(
            PortalSession.objects.select_for_update(skip_locked=True)
            .only(
                "id",
                "principal_id",
                "authenticated_at",
                "last_activity_at",
                "expires_at",
                "revoked_at",
                "version",
            )
            .filter(
                Q(revoked_at__isnull=False)
                | Q(expires_at__lte=now)
                | Q(last_activity_at__lte=now - ADMIN_IDLE)
            )
            .order_by("expires_at", "pk")[:batch_size]
        )
        pending = [row for row in rows if row.revoked_at is None]
        PortalSession.objects.filter(pk__in=[row.pk for row in pending]).update(
            revoked_at=Greatest(
                Value(now), F("last_activity_at"), F("authenticated_at")
            ),
            version=F("version") + 1,
        )
        AuditEvent.objects.bulk_create(
            [
                AuditEvent(
                    event_type="admin_timeout",
                    actor_id=row.principal_id,
                    subject_id=row.pk,
                )
                for row in pending
            ]
        )
        identifiers = [row.pk for row in rows]
        if identifiers:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT public.stewardship_admin_session_purge_v1(%s::uuid[])",
                    [identifiers],
                )
        PortalSession.objects.filter(pk__in=identifiers).delete()
        return len(rows)


def revoke_family_sessions(rows, *, now):
    """End locked Family rows and their audit envelopes in the owner's transaction."""
    from parishkit.stewardship.campaigns.credential_models import FamilySession

    if not connection.in_atomic_block:
        raise RuntimeError("Family revocation requires its owning transaction.")
    pending = [row for row in rows if row.revoked_at is None]
    FamilySession.objects.filter(pk__in=[row.pk for row in pending]).update(
        revoked_at=Greatest(Value(now), F("last_activity_at"), F("authenticated_at")),
        version=F("version") + 1,
    )
    AuditEvent.objects.bulk_create(
        [
            AuditEvent(
                event_type="family_session_ended",
                subject_id=row.pk,
                actor_id=row.family_id,
            )
            for row in pending
        ]
    )


def cleanup_family_sessions(*, batch_size=500):
    """Expire Family authority before removing protected metadata and parent rows."""
    from parishkit.stewardship.campaigns.credential_models import FamilySession
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.responses.baselines import cancel_session_baselines

    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Session cleanup requires a bounded batch size.")
    with work_transaction():
        now = database_now()
        rows = list(
            FamilySession.objects.select_for_update(of=("self",), skip_locked=True)
            .filter(
                Q(revoked_at__isnull=False)
                | Q(expires_at__lte=now)
                | Q(last_activity_at__lte=now - FAMILY_IDLE)
            )
            # The cleanup manifest owns Testing session/baseline/pin units
            # until cancellation releases the gate or its worker deletes them.
            .exclude(
                mode="testing", family__campaign__credential_state__go_live_gate=True
            )
            .order_by("expires_at", "pk")[:batch_size]
        )
        revoke_family_sessions(rows, now=now)
        identifiers = [row.pk for row in rows]
        keys = [row.session_id for row in rows]
        cancel_session_baselines(identifiers)
        FamilySession.objects.filter(pk__in=identifiers).delete()
        Session.objects.filter(pk__in=keys).delete()
        return len(rows)


def cleanup_anonymous_sessions(*, batch_size=500):
    """Clean expired OAuth sessions/one-use receipts without crossing live metadata."""
    from .auth_models import OAuthStateConsumption

    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Session cleanup requires a bounded batch size.")
    with transaction.atomic():
        now = database_now()
        keys = list(
            Session.objects.select_for_update(of=("self",), skip_locked=True)
            .filter(
                expire_date__lte=now,
                stewardship_portal__isnull=True,
                familysession__isnull=True,
            )
            .order_by("expire_date", "pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        Session.objects.filter(pk__in=keys).delete()
        receipts = list(
            OAuthStateConsumption.objects.select_for_update(skip_locked=True)
            .filter(expires_at__lte=now)
            .order_by("expires_at", "pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        OAuthStateConsumption.objects.filter(pk__in=receipts).delete()
        return len(keys), len(receipts)
