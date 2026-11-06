"""Signed-in sessions: every live interactive Admin session, and signing one out.

The Signed-in sessions page (#953, Administrator only) lists who is signed in
to the Admin portal right now and lets an Administrator end any one of those
sessions at once. A session is listed while it can still act: not yet
revoked, inside its absolute deadline, used within the idle limit
(``session_policy.ADMIN_IDLE``) and issued after the latest offline
Admin-access recovery (an earlier one carries a stale recovery epoch, which
admission refuses). Command sessions of the automation command line are
ordinary Admin sessions linked through ``AutomationLogin``; they are left
out, because Automation access lists and revokes their automation sessions.

Signing a session out revokes it exactly as admission does when access is
removed (``sessions._revoke`` with ``admin_revoked``, whose actor is the
person signed out, so it shows in their Sign-in activity), and records who
did it: ``admin_session_signed_out``, whose actor is the Administrator and
whose subject is the same session.
"""

from django.db import transaction
from django.db.models import Exists, OuterRef, Subquery

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.web.tables import Sorting

from .admin_session_store import stored_key
from .automation_models import AutomationLogin
from .models import PortalSession
from .policy_models import AdminRevocation, PortalUser
from .session_policy import ADMIN_IDLE
from .sessions import _revoke, database_now


def _live(query, now):
    """Narrow ``query`` to interactive sessions that can still act at ``now``."""
    query = query.filter(
        revoked_at__isnull=True,
        expires_at__gt=now,
        last_activity_at__gt=now - ADMIN_IDLE,
    ).exclude(Exists(AutomationLogin.objects.filter(portal_session_id=OuterRef("pk"))))
    latest = AdminRevocation.objects.order_by("-created_at", "-id").first()
    if latest is not None:
        # Admission compares each session's recovery epoch with the latest
        # recovery; a session issued before it is already refused.
        query = query.filter(created_at__gt=latest.created_at)
    return query


def live_principal(session):
    """The ``PortalUser`` id of ``session`` while it is live, else None.

    Read-only: it renews and revokes nothing. The refusal log (#1403) names
    the signed-in user of a request refused before its view ran admission,
    such as a CSRF failure, by the same live rules as this page.
    """
    key = stored_key(session)
    if key is None:
        return None
    query = PortalSession.objects.filter(session_id=key)
    return _live(query, database_now()).values_list("principal_id", flat=True).first()


def live_sessions(now, current_id):
    """Every live interactive Admin session, with its person's address.

    ``current_id`` is the reader's own PortalSession: that row is marked
    ``current`` and is never offered Sign out (the page's own Sign out ends
    it). Rows hold no session key, only the opaque PortalSession id.
    """
    email = PortalUser.objects.filter(pk=OuterRef("principal_id")).values("email")[:1]
    # Only the shown columns are read: never the Django session key
    # (``session_id``), which must not reach a page.
    rows = (
        _live(PortalSession.objects.all(), now)
        .annotate(email=Subquery(email))
        .order_by("-last_activity_at", "-id")
        .values(
            "id",
            "principal_id",
            "email",
            "authenticated_at",
            "last_activity_at",
            "expires_at",
        )
    )
    return [row | {"current": row["id"] == current_id} for row in rows]


# The table's sort orders: every data column, both directions; times sort
# newest first on a first click, and the default is the most recent activity.
SORTING = Sorting.by_column(
    {
        "user": lambda row: (row["email"] or "").casefold(),
        "signed_in": lambda row: row["authenticated_at"],
        "activity": lambda row: row["last_activity_at"],
        "expires": lambda row: row["expires_at"],
    },
    default="-activity",
    descending_first=("signed_in", "activity", "expires"),
)


def sign_out(session_id, actor, current_id):
    """End one live interactive session at once; returns whether this call did.

    The row is locked before its liveness is checked, so a concurrent ending
    (a timeout, a sign-out, another Administrator) either commits first and
    leaves nothing to do here, or waits for this one. A session that has
    already ended is left as it is. The reader's own current session, a
    command session and an unknown id are refused with LookupError.
    """
    with transaction.atomic():
        row = PortalSession.objects.select_for_update().filter(pk=session_id).first()
        if row is None or row.pk == current_id:
            raise LookupError("Signed-in session is unavailable.")
        if AutomationLogin.objects.filter(portal_session_id=row.pk).exists():
            raise LookupError("Signed-in session is unavailable.")
        now = database_now()
        if not _live(PortalSession.objects.filter(pk=row.pk), now).exists():
            return False
        _revoke(row, now, "admin_revoked")
        # Written as a literal type: the log descriptions guard finds direct
        # audit types by their ``event_type="..."`` in the source.
        AuditEvent.objects.create(
            event_type="admin_session_signed_out",
            actor_id=actor.identity,
            subject_id=row.pk,
        )
        return True
