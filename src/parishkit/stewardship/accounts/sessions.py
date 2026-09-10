"""Separate PostgreSQL cookie namespaces and live policy-based Admin sessions."""

import hashlib
import json
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Greatest
from django.middleware.csrf import rotate_token
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
        admin = request.path_info.startswith("/admin/")
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


def _authority_fingerprint(principal):
    """Detect privilege transitions without treating stored scopes as authority."""
    value = json.dumps(
        [sorted(principal.roles), sorted(principal.ministries)],
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(value).hexdigest()


def _rotate_authority(request, row, principal, now):
    """Replace a locked session without extending Google freshness or lifetime.

    Django's cycle_key deletes its protected parent, so create a new parent and
    metadata explicitly. Revoked metadata stays available for ordered cleanup.
    Concurrent requests holding the old cookie see only the revoked row.
    """
    _revoke(row, now, "admin_privileges_changed")
    session = import_module(settings.SESSION_ENGINE).SessionStore()
    session["principal"] = str(row.principal_id)
    session["recovery_epoch"] = request.session.get("recovery_epoch")
    session["authority_fingerprint"] = _authority_fingerprint(principal)
    session.set_expiry(row.expires_at)
    session.save()
    replacement = PortalSession.objects.create(
        session_id=session.session_key,
        principal_id=row.principal_id,
        authenticated_at=row.authenticated_at,
        last_activity_at=row.last_activity_at,
        expires_at=row.expires_at,
        actor_id=row.principal_id,
    )
    request.session = session
    rotate_token(request)
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
        request.session["principal"] = str(user_id)
        request.session["recovery_epoch"] = revocation_epoch()
        request.session["authority_fingerprint"] = _authority_fingerprint(principal)
        # Ordinary Google SSO may predate this application session. Its signed
        # time gates privileged actions, not the new session's absolute limit.
        request.session.set_expiry(now + ADMIN_ABSOLUTE)
        request.session.save()
        row = PortalSession.objects.create(
            session_id=request.session.session_key,
            principal_id=user_id,
            authenticated_at=authenticated_at,
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
        if request.session.get("authority_fingerprint") != _authority_fingerprint(
            principal
        ):
            # A read guard cannot write or rotate a cookie after headers start.
            # Its ordinary admission must first establish a current session.
            if read_only:
                return None
            row = _rotate_authority(request, row, principal, now)
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
        keys = [row.session_id for row in rows]
        PortalSession.objects.filter(pk__in=[row.pk for row in rows]).delete()
        Session.objects.filter(session_key__in=keys).delete()
        return len(rows)


def cleanup_family_sessions(*, batch_size=500):
    """Expire Family authority before removing protected metadata and parent rows."""
    from parishkit.stewardship.campaigns.credential_models import FamilySession

    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Session cleanup requires a bounded batch size.")
    with transaction.atomic():
        now = database_now()
        rows = list(
            FamilySession.objects.select_for_update(skip_locked=True)
            .filter(
                Q(revoked_at__isnull=False)
                | Q(expires_at__lte=now)
                | Q(last_activity_at__lte=now - FAMILY_IDLE)
            )
            .order_by("expires_at", "pk")[:batch_size]
        )
        pending = [row for row in rows if row.revoked_at is None]
        FamilySession.objects.filter(pk__in=[row.pk for row in pending]).update(
            revoked_at=Greatest(
                Value(now), F("last_activity_at"), F("authenticated_at")
            ),
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
        identifiers = [row.pk for row in rows]
        keys = [row.session_id for row in rows]
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
