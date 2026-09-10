"""Separate PostgreSQL cookie namespaces and live policy-based Admin sessions."""

from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date

from parishkit.stewardship.audit.models import AuditEvent

from .models import AdminRevocation, PortalSession, PortalUser
from .policy import current_principal

ADMIN_IDLE = timedelta(minutes=30)
ADMIN_ABSOLUTE = timedelta(hours=12)
FAMILY_IDLE = timedelta(minutes=60)
FAMILY_ABSOLUTE = timedelta(hours=4)


class NamespacedSessionMiddleware:
    """Only cookie transport is shared; no endpoint imports the other namespace."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.store = import_module(settings.SESSION_ENGINE).SessionStore

    def __call__(self, request):
        """Django save/expiry semantics without per-request global setting changes."""
        admin = request.path.startswith("/admin/")
        name, path = ("pk_admin", "/admin/") if admin else ("pk_family", "/")
        request.session = self.store(request.COOKIES.get(name))
        response = self.get_response(request)
        session = request.session
        if session.accessed:
            patch_vary_headers(response, ("Cookie",))
        if session.is_empty():
            if name in request.COOKIES:
                response.delete_cookie(path=path, key=name, samesite="Lax")
        elif session.modified and response.status_code < 500:
            age = session.get_expiry_age()
            try:
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


def database_now():
    """Use the authoritative database clock for session admission and expiry."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


def revocation_epoch():
    """Offline recovery invalidates earlier OAuth states and session metadata."""
    latest = AdminRevocation.objects.order_by("-created_at", "-id").first()
    return str(latest.pk) if latest else "initial"


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


def issue_admin(request, user_id, *, store):
    """Issue only after verified identity and fresh current-policy authorization."""
    principal = current_principal(store, user_id)
    if not principal.roles:
        raise PermissionError("Administration access is unavailable.")
    end_admin(request, reason="admin_reauthenticated")
    with transaction.atomic():
        now = database_now()
        request.session["principal"] = str(user_id)
        request.session["recovery_epoch"] = revocation_epoch()
        request.session.set_expiry(now + ADMIN_ABSOLUTE)
        request.session.save()
        row = PortalSession.objects.create(
            session_id=request.session.session_key,
            principal_id=user_id,
            authenticated_at=now,
            last_activity_at=now,
            expires_at=now + ADMIN_ABSOLUTE,
            actor_id=user_id,
        )
        AuditEvent.objects.create(
            event_type="admin_login", actor_id=user_id, subject_id=row.pk
        )
    return principal


def authenticated_admin(request, *, store, activity=False, read_only=False):
    """Re-evaluate policy every time; passive status/presence calls never renew idle."""
    if activity and read_only:
        raise ValueError("Read-only authorization cannot renew session activity.")
    with transaction.atomic():
        query = PortalSession.objects.all()
        if not read_only:
            query = query.select_for_update()
        row = query.filter(session_id=request.session.session_key).first()
        if row is None or row.revoked_at is not None:
            return None
        now = database_now()
        reason = None
        if now >= min(row.expires_at, row.last_activity_at + ADMIN_IDLE):
            reason = "admin_timeout"
        elif request.session.get("recovery_epoch") != revocation_epoch():
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
        if activity:
            PortalSession.objects.filter(pk=row.pk).update(
                last_activity_at=now,
                version=F("version") + 1,
            )
            row.last_activity_at = now
        request.portal_session = row
        request.principal = principal
        return principal


def require_fresh(request):
    """A fresh Google round trip, not a browser flag, admits privileged commands."""
    row = getattr(request, "portal_session", None)
    if (
        row is None
        or not 0 <= (database_now() - row.authenticated_at).total_seconds() <= 300
    ):
        raise PermissionError("Please authenticate with Google again.")
    return row.authenticated_at


def cleanup_admin_sessions(*, batch_size=500):
    """Delete protected metadata before parents, retaining opaque audit attribution."""
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Session cleanup requires a bounded batch size.")
    with transaction.atomic():
        now = database_now()
        rows = list(
            PortalSession.objects.select_for_update(skip_locked=True)
            .filter(
                Q(revoked_at__isnull=False)
                | Q(expires_at__lte=now)
                | Q(last_activity_at__lte=now - ADMIN_IDLE)
            )
            .order_by("expires_at", "pk")[:batch_size]
        )
        for row in rows:
            _revoke(row, now, "admin_timeout")
        keys = [row.session_id for row in rows]
        PortalSession.objects.filter(pk__in=[row.pk for row in rows]).delete()
        Session.objects.filter(session_key__in=keys).delete()
        return len(rows)
