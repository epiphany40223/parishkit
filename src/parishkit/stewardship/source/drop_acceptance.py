"""How a full refresh honors an Administrator's one-time acceptance (#530).

A full refresh is refused when a record or eligibility count falls too far
(``loading.check_source_counts``), and the refusal records every count it was
checked on (``drop_counts``). When an Administrator has confirmed in
ParishSoft that the change is real, System health records one acceptance of
that refusal (``drop_models.SourceDropAcceptance``, ADM-13 PR 5), with every
recorded count copied as reviewed. This module is the worker's side.

Only when a full load's drop check fails does the worker read the live
acceptance (``stewardship_source_drop_acceptance_active_v1()`` decides which
one is live, in SQL, for the guard and the worker alike). It reads it after
the download, so a full refresh already running when the Administrator
accepts can still use it. The load goes through only when ``honors`` holds:

- the refusal belongs to the same ParishSoft organization;
- every recorded count's new value is at least its reviewed after value, so
  nothing fell further than the Administrator saw;
- every count failing now also failed in the refusal, so a count that passed
  keeps the drop limit in effect.

The limit itself is never raised, and the before values are not compared
(the eligibility baselines move with every quick update). A load with no
Families or no Members is refused before this is asked, and quick updates
never ask. Anything else stays refused, which records a new refusal and so
ends the acceptance. The refresh that uses one records it in its manifest
(``ACCEPTANCE_KEY``) with the counts that failed, and promotion requires it
to be still live, under the work-order lock (``require_live``), so the
read and the promotion are one decision. After it, each count that failed
starts its trend again from that refresh (``refreshing._accepted_measures``)
and compares with the accepted value at the normal limit; every other count
keeps its week of history.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from django.db import connection, connections, transaction

from parishkit.stewardship.observability import Event, emit

from .loading import DestructiveSourceChange

# Where a full refresh's manifest load evidence names the acceptance it used.
ACCEPTANCE_KEY = "drop_acceptance"


@dataclass(frozen=True)
class Acceptance:
    """The live acceptance, as the worker needs it: counts and identifiers only."""

    id: UUID
    actor_id: UUID
    organization_id: int
    counts: dict


def reviewed_counts(checks):
    """The ``counts`` an acceptance copies from a refusal's recorded rows.

    ``checks`` are ``SourceDropCount`` rows or ``loading.CountCheck`` values;
    the shape is the one the SQL guard rebuilds from the recorded rows and
    requires the acceptance to equal exactly.
    """
    return {
        check.measure: {
            "before": check.before,
            "after": check.after,
            "failed": check.failed,
        }
        for check in checks
    }


def _reviewed(value):
    """Whether one reviewed count has the shape ``reviewed_counts`` gives."""
    return (
        type(value) is dict
        and type(value.get("after")) is int
        and type(value.get("failed")) is bool
    )


def honors(checks, counts):
    """Whether a full load's ``checks`` stay within the reviewed ``counts``.

    ``checks`` are every ``loading.CountCheck`` the new load was checked on;
    ``counts`` are the acceptance's reviewed counts. Every reviewed count
    must be checked again (otherwise it could not be shown not to have
    fallen further), must not be below its reviewed after value, and may
    fail now only if it failed in the refusal. A count the refusal did not
    record may not fail. Malformed reviewed counts honor nothing.
    """
    if type(counts) is not dict or not all(map(_reviewed, counts.values())):
        return False
    if set(counts) - {check.measure for check in checks}:
        return False
    for check in checks:
        seen = counts.get(check.measure)
        if seen is None:
            if check.failed:
                return False
        elif check.after < seen["after"] or (check.failed and not seen["failed"]):
            return False
    return True


def live_acceptance():
    """The live acceptance with its refusal's organization, or ``None``.

    The SQL lifetime check chooses the row and a second statement reads it.
    Acceptance rows never change, so that is the row the check chose, though
    it may have stopped being live in between; promotion checks again under
    the work-order lock (``require_live``).
    """
    from .drop_models import SourceDropAcceptance

    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SELECT public.stewardship_source_drop_acceptance_active_v1()")
        (identifier,) = cursor.fetchone()
        if identifier is None:
            return None
        row = SourceDropAcceptance.objects.select_related("attempt__request").get(
            pk=identifier
        )
    return Acceptance(
        row.pk, row.actor_id, row.attempt.request.organization_id, row.counts
    )


def acceptor(organization_id, task_id):
    """The callback a full load asks when its drop check fails.

    ``accept(checks)`` returns the manifest evidence to record (the
    acceptance, who gave it and the counts that failed) when the live
    acceptance honors ``checks``, else ``None`` and the refusal stands. It
    runs between provider requests, so it closes its database connection
    again, as the source transport does: no connection is held across
    ParishSoft HTTP. A database error propagates, so the attempt fails
    without recording a refusal that would end the acceptance.
    """

    def accept(checks):
        """Read the live acceptance once and decide; see ``acceptor``."""
        try:
            acceptance = live_acceptance()
        finally:
            connections.close_all()
        if (
            acceptance is None
            or acceptance.organization_id != organization_id
            or not honors(checks, acceptance.counts)
        ):
            return None
        # Identifiers only: the operator sees in ordinary output that this
        # refresh's refused counts were honored under an Administrator's
        # acceptance.
        emit(
            Event.TASK_STARTED,
            level=logging.WARNING,
            task_id=task_id,
            source_drop_acceptance=acceptance.id,
        )
        return {
            "id": str(acceptance.id),
            "actor_id": str(acceptance.actor_id),
            # The counts this acceptance let fall: only their trend starts
            # again after this refresh (refreshing._accepted_measures).
            "failed": sorted(check.measure for check in checks if check.failed),
        }

    return accept


class AcceptanceLapsed(DestructiveSourceChange):
    """A refresh loaded under an acceptance that is no longer live.

    The acceptance expired, or a refusal or another full refresh's promotion
    ended it, between the load and the promotion. The refresh is refused
    like any other drop refusal, and there is nothing new to record. That
    refusal can never be accepted again (one acceptance per refused attempt);
    the next full refresh records a new refusal, and that one can be.
    ``acceptance`` is the lapsed acceptance's UUID (``None`` if the manifest
    named it malformed), which the refusal's process-log line names
    (``failures.settle_failed_read``).
    """

    def __init__(self, message, *, acceptance=None):
        """Keep the lapsed acceptance's identifier beside the fixed message."""
        super().__init__(message)
        self.acceptance = acceptance if isinstance(acceptance, UUID) else None


def require_live(cursor):
    """Refuse promoting a refresh whose manifest names a lapsed acceptance.

    ``cursor`` is the snapshot being promoted's manifest. Runs inside the
    promotion's work-order effect (``execution._promote``), so the check and
    the promotion are one decision: a refusal or another promotion, which
    end an acceptance, run under the same lock. A manifest that names no
    acceptance is not checked.
    """
    load = cursor.get("load") if type(cursor) is dict else None
    accepted = load.get(ACCEPTANCE_KEY) if type(load) is dict else None
    if accepted is None:
        return
    with connection.cursor() as database:
        database.execute("SELECT public.stewardship_source_drop_acceptance_active_v1()")
        (identifier,) = database.fetchone()
    if type(accepted) is not dict or str(identifier) != accepted.get("id"):
        raise AcceptanceLapsed(
            "The large-change acceptance this refresh used is no longer in effect.",
            acceptance=_uuid(accepted.get("id") if type(accepted) is dict else None),
        )


def _uuid(value):
    """``value`` as a UUID, or ``None`` when it is not one."""
    try:
        return UUID(value) if type(value) is str else None
    except ValueError:
        return None
