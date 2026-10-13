"""``events list`` and ``events acknowledge``: Home's acknowledgements (ADM-11).

The Admin home page shows two things an Administrator acknowledges: the
**Security events awaiting acknowledgement** panel (``security_events``: an
expansion of who may sign in, or a replaced backup key) and the **critical
problems** banner (``audit.critical_events``: CRITICAL operational log rows
of the last day). The commands read and acknowledge them through the pages'
own pieces, so the page and the command line cannot drift.

``events list`` admits passively with ``CONFIGURE`` (both are shown to
Administrators only), so a read-only session reads, and records nothing,
as the home page records nothing. The security events are the session's
own Administrator's, as that Administrator's dashboard shows them; the
actor is named by account ID rather than the address the page shows. The
banner's groups come with the banner's own signed list of the rows it
counted (``token``), which ``events acknowledge --critical`` takes, so an
acknowledgement hides only what was listed.

``events acknowledge`` is the pages' POST: admitted with the page's own
capability (``MANAGE_USERS`` for a security event, ``SYSTEM_LOGS`` for the
banner), recording activity and so needing a full-scope session, through
the views' request-free ``acknowledge_now``. The pages ask for no recent
sign-in and no confirmation, so it is neither fresh-gated nor prompting.
It records the page's own event and ``admin_cmd_events_acknowledge`` in the
page's transaction, only when something was acknowledged.
"""

from dataclasses import dataclass

from .admin_reads import ReadModel, _held, _recheck

# The longest ``--critical`` token: the banner signs at most 500 row ids,
# compressed, so a genuine one is far shorter.
TOKEN_LIMIT = 64_000


@dataclass(frozen=True)
class EventsList(ReadModel):
    """What Home asks this Administrator to acknowledge.

    ``security`` lists the security events still on the session
    Administrator's dashboard, newest first: ``id``, ``kind``, ``name``
    (the page's words), ``target`` (the address or domain the page names),
    ``before`` and ``after`` (role names), ``key_before`` and ``key_after``
    (key IDs, for a replaced backup key only), ``created_at`` and
    ``actor_id``. ``critical`` is the banner: ``count``, ``all_ended``,
    ``since`` (the window's start), ``events`` (``event``, ``name``,
    ``count``, ``ended_at``, in the banner's order), ``token`` (the signed
    rows to acknowledge, or None) and ``acknowledgeable`` (how many rows
    the token covers: at most 500, the rest wait for the next one).
    """

    security: list
    critical: dict


@dataclass(frozen=True)
class EventsAcknowledgement(ReadModel):
    """``events acknowledge``'s outcome.

    ``kind`` is ``security`` or ``critical``; ``event_id`` the security
    event (None for ``critical``); ``acknowledged`` how many were newly
    recorded, 0 when nothing changed.
    """

    kind: str
    event_id: object
    acknowledged: int


def security_model(row):
    """One ``open_events`` row as the command prints it."""
    keys = row["key_ids"] or (None, None)
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": str(row["label"]),
        "target": row["target"],
        # The page's role names are lazily translated; print them as text.
        "before": [str(role) for role in row["before"]],
        "after": [str(role) for role in row["after"]],
        "key_before": keys[0],
        "key_after": keys[1],
        "created_at": row["created_at"],
        "actor_id": row["actor_id"],
    }


def critical_model(counts, ids, ended, since):
    """The banner as the command prints it, from ``alert_counts``'s answer.

    The groups and their order are the banner's (``summary``); ``token`` is
    the banner form's signed list (``sign``) of the oldest ``ids``, at most
    ``ACKNOWLEDGE_LIMIT``, or None when nothing awaits.
    """
    from .audit.critical_events import sign, summary

    return {
        "count": sum(counts.values()),
        "all_ended": bool(counts) and counts.keys() <= ended.keys(),
        "since": since,
        "events": [
            {
                "event": group["event"],
                "name": group["label"],
                "count": group["count"],
                "ended_at": group["ended_at"],
            }
            for group in summary(counts, ended)
        ],
        "token": sign(ids) if ids else None,
        "acknowledgeable": len(ids),
    }


def read_events(caller, service):
    """``events list``: Home's security events and critical banner.

    Admits passively with ``CONFIGURE``, reads both in one read transaction
    through the page's own ``open_events`` and ``alert_counts`` over the
    banner's window, and rechecks the session before reporting.
    """
    from .accounts.policy import Capability
    from .accounts.security_events import open_events
    from .accounts.sessions import database_now
    from .admin_reads import _admit
    from .audit.critical_events import ACKNOWLEDGE_LIMIT, WINDOW
    from .campaigns.work_locks import read_transaction
    from .jobs.delivery_metadata import alert_counts

    def step():
        """Admit, read both panels in one snapshot, recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        with read_transaction():
            security = open_events(actor)
            since = database_now() - WINDOW
            counts, ids, _, ended = alert_counts(since, limit=ACKNOWLEDGE_LIMIT)
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return EventsList(
            security=[security_model(row) for row in security],
            critical=critical_model(counts, ids, ended, since),
        )

    return _held(step)


def _admit_change(caller, service, capability):
    """The page's POST admission with its capability, recording activity.

    A session that ended is exit 5 (``session_ended``), not the page's
    signed-out refusal.
    """
    from .accounts.admin_editing import principal
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin

    try:
        return principal(caller, service, capability=capability)
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def acknowledge(caller, service, *, event_id=None, token=None, context):
    """``events acknowledge``: one security event, or the listed banner rows.

    Exactly one of ``event_id`` and ``token`` is given. Runs the view's own
    ``acknowledge_now`` with a ``record`` callback that writes
    ``admin_cmd_events_acknowledge`` in its transaction when something was
    acknowledged. An unknown event is ``not_available``; an altered token
    ``invalid``; a configuration refusal (a restore under review)
    ``unavailable``. A database failure once the command's row was written
    may have struck the commit, so ``context["committed"]`` is set (exit 6).
    """
    from django.core import signing
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts import critical_event_views, security_event_views
    from .accounts.policy import Capability
    from .admin_reads import NotAvailable, Unavailable
    from .audit.critical_events import shown
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    if event_id is not None:
        kind, capability = "security", Capability.MANAGE_USERS
    else:
        kind, capability = "critical", Capability.SYSTEM_LOGS
    # Admit first, then read the token, as the page's view does.
    actor = _admit_change(caller, service, capability)
    if kind == "critical":
        try:
            ids = shown(token)
        except signing.BadSignature:
            raise ValueError("The acknowledgement token is not valid.") from None
    written = []

    def record():
        """Record the command's event, attributed to the session."""
        record_action(
            Action.ADMIN_CMD_EVENTS_ACKNOWLEDGE,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        written.append(True)

    try:
        if kind == "security":
            count = int(
                security_event_views.acknowledge_now(
                    service, actor, event_id, record=record
                )
            )
        else:
            count = critical_event_views.acknowledge_now(
                service, actor, ids, record=record
            )
    except LookupError:
        if kind != "security":
            raise
        raise NotAvailable("The security event does not exist.") from None
    except ConfigError:
        raise Unavailable("The configuration cannot be changed now.") from None
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        raise
    except PermissionError:
        # A session that ended meanwhile is exit 5, not the page's refusal.
        _recheck(caller, service.store, actor, capability)
        raise
    context["committed"] = bool(written)
    return EventsAcknowledgement(kind=kind, event_id=event_id, acknowledged=count)
