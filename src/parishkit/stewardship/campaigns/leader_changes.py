"""Count-only audit of changes to who leads which Ministry (#1034).

Since #922 a Ministry leader's Ministries come from ParishSoft roster roles,
recomputed on every request by ``stewardship_ministry_leaders_v1``. A
ParishSoft refresh that adds or removes a role or changes a leader's address,
or a settings change to the campaign's leader roles, Ministries or Ministry
activity, therefore widens or narrows what a Ministry leader sees. The owner
of each such change (source promotion and configuration activation) reads the
three pointers the definition depends on (current source snapshot, active
configuration, current campaign) just before its change, and just after it,
in the same transaction, calls ``stewardship_ministry_leader_changes_v1``
(migration 0070).

That definer compares the (address, Ministry) pairs then and now and, when
any differ, writes one ``ministry_leaders_changed`` audit event itself:
``count`` is the pairs gained and ``matching_count`` the pairs lost (the
existing audit context keys). It returns nothing, so neither login ever reads
a leader, an address or even the counts (which would otherwise be an
aggregate oracle for a login able to stage changes it rolls back). A login
without EXECUTE on it (bootstrap, offline recovery), or a schema without it,
records nothing.

Recording is best-effort: a failure is logged and the owning change goes on,
except a SQL time limit, which propagates so the owner's own timeout handling
records it (``source.snapshots.promote_snapshot`` bounds every statement,
the count included).
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.observability import current_correlation

# The recorder (migration 0070), as to_regprocedure names it.
RECORDER = (
    "public.stewardship_ministry_leader_changes_v1"
    "(uuid, uuid, uuid, uuid, uuid, uuid, uuid)"
)
# The pointers before a change, and whether this login may record at all.
# The recorder is absent on the schema `migrate --users-upgrade-base` leaves
# (it follows the users swap), so its absence answers False, not an error.
POINTERS_SQL = (
    "SELECT CASE WHEN to_regprocedure(%s) IS NULL THEN false "
    "ELSE has_function_privilege(to_regprocedure(%s), 'EXECUTE') END, "
    "(SELECT snapshot_id FROM public.stewardship_source_current "
    "WHERE singleton), "
    "(SELECT active_configuration_id FROM public.stewardship_system_configuration "
    "LIMIT 1), "
    "(SELECT current_campaign_id FROM public.stewardship_system_configuration "
    "LIMIT 1)"
)
RECORD_SQL = (
    "SELECT public.stewardship_ministry_leader_changes_v1(%s, %s, %s, %s, %s, %s, %s)"
)


@dataclass(frozen=True)
class Pointers:
    """What the leader definition read just before a change."""

    snapshot: UUID | None
    configuration: UUID | None
    campaign: UUID | None


def leader_pointers():
    """The pointers before a change, or None when nothing can be counted.

    None means "no comparison": the recorder is not installed or the login
    lacks EXECUTE on it, or the read failed (logged at WARNING). The read
    runs in a savepoint so a failure leaves the caller's transaction usable.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(POINTERS_SQL, [RECORDER, RECORDER])
            granted, *pointers = cursor.fetchone()
    except DatabaseError as error:
        _absorb(error)
        return None
    return Pointers(*pointers) if granted else None


def record_leader_changes(
    before, *, actor_id=None, subject_id=None, promoted_snapshot=None
):
    """Have SQL record one ``ministry_leaders_changed`` event if leaders changed.

    ``before`` is ``leader_pointers()`` from just before the owner's change,
    in the same transaction. ``subject_id`` names what changed them (the
    refresh task or the configuration request), ``actor_id`` the worker,
    and ``promoted_snapshot`` a promotion's new snapshot. The definer decides
    and writes the event; nothing comes back.
    """
    if before is None:
        return
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                RECORD_SQL,
                [
                    before.snapshot,
                    before.configuration,
                    before.campaign,
                    actor_id,
                    subject_id,
                    current_correlation(),
                    promoted_snapshot,
                ],
            )
    except DatabaseError as error:
        _absorb(error)


def _absorb(error):
    """Log a failed read or record and let the owning change continue.

    A SQL time limit is re-raised: the owner bounds its statements so a
    pathological one fails the attempt (which retries), and its timeout
    handling records what stopped, the limit and the time spent.
    """
    from parishkit.stewardship.jobs.broker import sql_timeout_kind
    from parishkit.stewardship.observability import emit_failure

    if sql_timeout_kind(error) is not None:
        raise error
    emit_failure(error, level=logging.WARNING)
