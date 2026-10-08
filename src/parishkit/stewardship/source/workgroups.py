"""The campaign's Reminder WorkGroup (#861): Families that get no Reminders.

A campaign may name one ParishSoft Family WorkGroup in its optional
``reminder_workgroup`` setting. Every source refresh (full and quick) lists
the Family WorkGroups and reads the membership of only that one, and records
what it found in its snapshot's load evidence under ``EVIDENCE_KEY``:
``{"name": ..., "found": bool, "family_duids": [...]}``. Families in it are
still part of the campaign in every way (portal access, codes, links,
counts, the invitation, receipts and confirmations); planning only skips
their Reminders (``campaigns.family_schedule_planning``).

The current snapshot's evidence counts only while its name is the one the
campaign names now: after the Administrator changes or clears the setting,
the old membership no longer applies, and the new one does from the next
refresh.
"""

import logging

from parishkit.parishsoft import (
    load_family_workgroup_memberships,
    load_family_workgroups,
)
from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP

EVIDENCE_KEY = "reminder_workgroup"


def configured_name(values):
    """The WorkGroup name a campaign's configuration values name, or None."""
    return values.get(REMINDER_WORKGROUP) or None


def load_reminder_workgroup(client, name):
    """Read the named Family WorkGroup's member Family DUIDs from ParishSoft.

    Lists the Family WorkGroups (one paged call), then fetches the membership
    of only those whose name matches ``name`` (normally one). Names match
    ignoring case and surrounding spaces, so a stray capital in ParishSoft
    does not silently turn the setting off. Returns the evidence a snapshot
    records, or None when no name is configured (nothing is fetched).
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
    return {"name": name, "found": bool(matches), "family_duids": sorted(duids)}


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
    value = load.get(EVIDENCE_KEY) if type(load) is dict else None
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


def current_evidence(values):
    """``(name, evidence)``: the campaign's WorkGroup name and what applies.

    ``evidence`` is the current snapshot's record for that same name, or
    None when no name is set or no refresh has read it yet.
    """
    from .snapshot_models import SourceCurrent, SourceSnapshot

    name = configured_name(values)
    if name is None:
        return None, None
    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    if snapshot is None:
        return name, None
    evidence = recorded(
        SourceSnapshot.objects.values_list("cursor", flat=True).get(pk=snapshot)
    )
    if evidence is None or evidence["name"] != name:
        return name, None
    return name, evidence


def excluded_duids(values):
    """The Family DUIDs whose Reminders the campaign skips now (may be empty)."""
    _name, evidence = current_evidence(values)
    if evidence is None:
        return frozenset()
    return frozenset(evidence["family_duids"])
