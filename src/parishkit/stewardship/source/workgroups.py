"""The campaign's Reminder WorkGroup (#861): Families that get no Reminders.

A campaign may name one ParishSoft Family WorkGroup in its optional
``reminder_workgroup`` setting. Every source refresh (full and quick) lists
the Family WorkGroups and reads the membership of only that one, and records
what it found in its snapshot's load evidence under ``EVIDENCE_KEY``:
``{"name": ..., "found": bool, "family_duids": [...]}``. Families in it are
still part of the campaign in every way (portal access, codes, links,
counts, the invitation, receipts and confirmations); planning only skips
their Reminders (``campaigns.family_schedule_planning``).

Which Families are excluded (``applied_evidence``, #1305): clearing the
setting stops every exclusion at once. A new name applies from the next
refresh that reads it; until then a rename keeps excluding the Families the
current snapshot recorded for the previous name, so a corrected spelling or
next year's WorkGroup never sends a Reminder to every Family meant to be
skipped. A snapshot that recorded no WorkGroup (none was set when it was
read) excludes nobody, so the first name set applies from the next refresh.
A refresh that finds no WorkGroup by the name set (a misspelled rename, or a
WorkGroup deleted in ParishSoft) records ``found: false`` but keeps the
Families the snapshot before it applied (``keep_when_missing``), so a typo
never sends a Reminder to every Family meant to be skipped; the page and
System health show the missing name. A WorkGroup that is found but has no
members excludes nobody: that is the Administrator's intent. When ``found``
is false, ``family_duids`` is therefore the set kept from before, not a
membership ParishSoft reported.
``current_evidence`` answers the narrower question of whether the newest
refresh has read the name set now.
"""

import logging

from parishkit.parishsoft import (
    load_family_workgroup_memberships,
    load_family_workgroups,
)
from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP

from .canonical import SourceReferenceSkew

EVIDENCE_KEY = "reminder_workgroup"


def configured_name(values):
    """The WorkGroup name a campaign's configuration values name, or None."""
    return values.get(REMINDER_WORKGROUP) or None


def load_reminder_workgroup(client, name, *, families=None):
    """Read the named Family WorkGroup's member Family DUIDs from ParishSoft.

    Lists the Family WorkGroups (one paged call), then fetches the membership
    of only those whose name matches ``name`` (normally one). Names match
    ignoring case and surrounding spaces, so a stray capital in ParishSoft
    does not silently turn the setting off. Returns the evidence a snapshot
    records, or None when no name is configured (nothing is fetched).

    With a key that reaches several ParishSoft organizations (#1554) the list
    keeps only the configured organization's WorkGroups (the client drops
    the others), and ``families``, the full load's Family keys, cross-checks
    the membership: a Family the load does not hold fails the refresh as a
    dangling reference, since it could be another parish's. A key that
    reaches one organization is read exactly as before.
    """
    if name is None:
        return None
    wanted = name.strip().casefold()
    matches = {
        duid: workgroup
        for duid, workgroup in load_family_workgroups(client).items()
        if str(workgroup["name"]).strip().casefold() == wanted
    }
    duids = set()
    for workgroup in load_family_workgroup_memberships(client, matches).values():
        for row in workgroup["membership"]:
            duid = row.get("py family duid")
            if type(duid) is int and 1 <= duid < 2**63:
                duids.add(duid)
    if (
        families is not None
        and (client.organizations_reached or 0) > 1
        and any(str(duid) not in families for duid in duids)
    ):
        raise SourceReferenceSkew("Source WorkGroup has no retained Family.")
    return {"name": name, "found": bool(matches), "family_duids": sorted(duids)}


def keep_when_missing(evidence, base):
    """A refresh's WorkGroup evidence, keeping the earlier set when not found.

    ``evidence`` is what this refresh read (``load_reminder_workgroup``) and
    ``base`` what the current snapshot recorded (``recorded``), or None. When
    ParishSoft has no WorkGroup by the configured name, the Families ``base``
    applied stay applied (#1305): a misspelled rename then keeps the old set
    until a refresh finds the named WorkGroup. A found WorkGroup, even an
    empty one, replaces the set.
    """
    if evidence is None or evidence["found"] or base is None:
        return evidence
    return {**evidence, "family_duids": list(base["family_duids"])}


def warn_if_missing(evidence, *, task_id):
    """Log an operational WARNING when the configured name matched nothing.

    The line carries only the closed failure category, never the name.
    """
    from parishkit.stewardship.observability import Event, FailureKind, emit

    if evidence is not None and not evidence["found"]:
        emit(
            Event.TASK_STARTED,
            level=logging.WARNING,
            task_id=task_id,
            failure_kind=FailureKind.REMINDER_WORKGROUP_MISSING,
        )


def recorded(cursor):
    """The Reminder WorkGroup evidence a snapshot cursor recorded, or None."""
    load = cursor.get("load") if type(cursor) is dict else None
    return valid_evidence(load.get(EVIDENCE_KEY) if type(load) is dict else None)


def valid_evidence(value):
    """``value`` when it has the evidence's exact shape, else None.

    System health reads the evidence alone from the cursor, in the
    statement Home already runs (``system_health.HEALTH_SQL``), and checks
    it here as ``recorded`` does.
    """
    if (
        type(value) is dict
        and set(value) == {"name", "found", "family_duids"}
        and type(value["name"]) is str
        and type(value["found"]) is bool
        and type(value["family_duids"]) is list
        and all(type(duid) is int for duid in value["family_duids"])
    ):
        return value
    return None


def snapshot_evidence():
    """The WorkGroup evidence the current snapshot recorded, or None."""
    from .snapshot_models import SourceCurrent, SourceSnapshot

    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    if snapshot is None:
        return None
    return recorded(
        SourceSnapshot.objects.values_list("cursor", flat=True).get(pk=snapshot)
    )


def applied_evidence(values):
    """The evidence whose Families the campaign's Reminders skip now, or None.

    None when no name is set: a clear applies at once. Otherwise the current
    snapshot's evidence, whichever name it was read for: after a rename it is
    still the previous name's until a refresh reads the new one (#1305). Its
    ``name`` differs from the configured one exactly while that rename waits.
    """
    if configured_name(values) is None:
        return None
    return snapshot_evidence()


def current_evidence(values):
    """``(name, evidence)``: the campaign's WorkGroup name and its evidence.

    ``evidence`` is the current snapshot's record for that same name, or
    None when no name is set or no refresh has read it yet (``pending``).
    Which Families are excluded meanwhile is ``applied_evidence``'s answer.
    """
    name = configured_name(values)
    return name, evidence_for(name, applied_evidence(values))


def evidence_for(name, applied):
    """``applied`` when it was read for ``name``, else None (``pending``).

    The pure half of ``current_evidence``, so a caller that already holds
    ``applied_evidence``'s answer need not read the snapshot again.
    """
    if applied is None or applied["name"] != name:
        return None
    return applied


def excluded_duids(values):
    """The Family DUIDs whose Reminders the campaign skips now (may be empty)."""
    evidence = applied_evidence(values)
    if evidence is None:
        return frozenset()
    return frozenset(evidence["family_duids"])
