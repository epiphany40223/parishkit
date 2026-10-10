"""Families whose ParishSoft data keeps the Family form from opening (#774).

A Family whose ParishSoft record holds a Member value the form cannot accept
(an unparseable birth date, a name over the length limit, a value outside a
closed list) cannot open the Family form at all. This lists every such
Family of the current campaign, for an Administrator to correct in
ParishSoft, from the same check ``pk-stewardship source-form-check`` runs
(``source_form_check.member_source_findings`` over ``scan_inputs``), so the
page and the command never disagree, and it includes Families that have not
tried the form yet.

It never shows a Member value, not even a name, since the refusing field may
be the name itself: only the Family name and DUID, the Member DUID and the
field in plain words. The Family name is therefore the surname alone
(``family_display_name``), never the usual "Surname, heads" name, whose
heads' names are Member values: a head's over-long or unreadable name could
be the very value the form refuses (#932).

The scan reads the whole current snapshot's Members once, so its result is
kept in process memory, keyed by the campaign, the snapshot and the campaign
configuration: a ParishSoft refresh or a campaign change is a new key, so
the cached answer can never outlive the data it describes. Snapshot and
configuration ids are random UUIDs, never reused, and a restore runs with
the application down (operations spec, Backup), so web
processes start again with an empty cache.
"""

from collections import OrderedDict
from threading import Lock

from parishkit.stewardship.responses.inputs import member_source_fields
from parishkit.stewardship.source.family_names import family_display_name
from parishkit.stewardship.source.version_models import SnapshotFamily
from parishkit.stewardship.source_form_check import (
    RECORD,
    member_source_findings,
    scan_identity,
    scan_inputs,
)

# How many results to keep: a few campaigns or snapshots in flight is plenty.
CACHE_SIZE = 4
_cache = OrderedDict()
_lock = Lock()
# A finding about the Member record itself, not one of its fields.
RECORD_LABEL = "The Member record itself (an unusable or repeated Member)"


def field_label(name, kind):
    """The field to correct, in the census form's own plain words."""
    if name == "member":
        return RECORD_LABEL
    labels = {field.name: field.label for field, _ in member_source_fields(True)}
    label = labels.get(name, name.replace("_", " ").capitalize())
    if kind == RECORD:
        return f"{label} (its ParishSoft contact record cannot be read)"
    return label


def cache_key(inputs, campaign_id):
    """What identifies one result: the campaign, the snapshot and its setup."""
    return (
        str(campaign_id),
        str(inputs["snapshot_id"]),
        str(inputs["configuration_id"]),
    )


def _remember(key, value):
    """Keep ``value`` for ``key``, dropping the oldest beyond ``CACHE_SIZE``."""
    with _lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def _cached(key):
    """The remembered result for ``key``, or None."""
    with _lock:
        return _cache.get(key)


def family_names(snapshot_id, duids):
    """Each Family's surname from the snapshot, by the shared naming rule.

    Deliberately not ``snapshot_family_names``: its heads' names are Member
    values, which this page never shows (see the module docstring).
    """
    if not duids:
        return {}
    return {
        row.source_key: family_display_name(
            row.payload.payload or {}, "Unavailable Family"
        )
        for row in SnapshotFamily.objects.filter(
            snapshot_id=snapshot_id, source_key__in=[str(duid) for duid in duids]
        ).select_related("payload")
    }


def blocked_families(campaign_id):
    """Every finding for the campaign's eligible Families, worded for the page.

    Runs inside the caller's read guard. Returns ``{"eligible": n, "rows":
    [...]}``, one row per finding, sorted by Family then Member.
    """
    found = _cached(cache_key(scan_identity(campaign_id), campaign_id))
    if found is not None:
        return found
    inputs = scan_inputs(campaign_id)
    # The inputs' own identity, in case a refresh promoted in between. If the
    # source moved again while the Members were read (READ COMMITTED), the
    # result may mix two snapshots: answer it, but do not keep it.
    key = cache_key(inputs, campaign_id)
    keep = cache_key(scan_identity(campaign_id), campaign_id) == key
    findings = member_source_findings(
        inputs["families"],
        inputs["members"],
        inputs["contacts"],
        census=inputs["census"],
    )
    names = family_names(
        inputs["snapshot_id"], {item["family_duid"] for item in findings}
    )
    rows = [
        {
            "family_duid": item["family_duid"],
            "family_name": names.get(str(item["family_duid"]), "Unavailable Family"),
            "member_duid": item["member_duid"],
            "field": field_label(item["field"], item["kind"]),
        }
        for item in findings
    ]
    result = {
        "eligible": len(inputs["families"]),
        "families": len({row["family_duid"] for row in rows}),
        "rows": rows,
    }
    if keep:
        _remember(key, result)
    return result
