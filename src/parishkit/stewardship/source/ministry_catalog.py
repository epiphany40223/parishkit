"""ParishSoft Ministry catalog changes between promoted snapshots (#342).

Ministries are keyed by DUID, and only a full refresh re-reads the catalog.
When staging validates a corpus that has a base snapshot, it compares the two
catalogs by DUID and keeps the differences in the new snapshot's cursor under
``ministry_catalog``, next to the changed-record counts (#242). The web role
can already read that column, manifests survive compaction, and the SQL
completion guard checks only its own cursor keys, so this needs no schema,
grant or operational-event change (the event list is a SQL constraint, frozen
for launch). The Admin home page reads those records back for a notice.

The record is display-only. It never changes a campaign's Ministry
selections, Ministry activity or anything a Family sees.
"""

import json
import logging
from datetime import timedelta

from django.db import connection, transaction

from parishkit.stewardship.observability import Event, emit_failure

from .catalog_names import clean_ministry_name
from .snapshot_models import SourceCurrent, SourceSnapshot
from .version_models import SnapshotMinistry, SourceMinistry

CURSOR_KEY = "ministry_catalog"
KINDS = ("added", "removed", "renamed")
# Each list keeps this many entries; the counts always hold the full number.
MAX_LISTED = 50
# The home page shows changes from refreshes promoted this recently, at most
# this many refreshes. Full refreshes run daily, so a week of them fits.
WINDOW = timedelta(days=7)
MAX_REFRESHES = 5
# Some parishes retire a Ministry by renaming it with this prefix. It is only
# a hint on the notice ("possibly retired"); nothing acts on it.
RETIRED_PREFIX = "X-"
# Campaign states whose Ministry selections Families can still be shown.
OPEN_STATES = frozenset({"draft", "scheduled", "active"})


def _name(duid, raw):
    """The display name, cleaned by the same rule as every Ministry page.

    Uses the non-logging cleaner: a staging comparison or a dashboard read
    is not a place to repeat the repaired-name warning.
    """
    return clean_ministry_name(duid, raw)[0]


def retired_name(name):
    """Whether a display name carries the retired-Ministry prefix hint."""
    return name[: len(RETIRED_PREFIX)].upper() == RETIRED_PREFIX.upper()


def catalog_names(snapshot_id):
    """Map each Ministry DUID in one snapshot to its raw ParishSoft name."""
    rows = SnapshotMinistry.objects.filter(snapshot_id=snapshot_id).values_list(
        "source_key", "payload__canonical"
    )
    return {int(key): json.loads(text).get("name") for key, text in rows}


def catalog_changes(before, after):
    """Compare two ``{duid: raw name}`` catalogs; None when nothing changed.

    Added and removed entries carry the name from the catalog that has the
    DUID; renamed entries carry both. Names are compared after cleaning, so a
    change the Admin could not see (such as trailing spaces) is not reported.
    Lists are in DUID order and capped at ``MAX_LISTED``; ``counts`` holds the
    full totals.
    """
    names_before = {duid: _name(duid, raw) for duid, raw in before.items()}
    names_after = {duid: _name(duid, raw) for duid, raw in after.items()}
    found = {
        "added": [
            {"duid": duid, "name": names_after[duid]}
            for duid in sorted(names_after.keys() - names_before.keys())
        ],
        "removed": [
            {"duid": duid, "name": names_before[duid]}
            for duid in sorted(names_before.keys() - names_after.keys())
        ],
        "renamed": [
            {"duid": duid, "before": names_before[duid], "name": names_after[duid]}
            for duid in sorted(names_before.keys() & names_after.keys())
            if names_before[duid] != names_after[duid]
        ],
    }
    if not any(found.values()):
        return None
    return {
        "counts": {kind: len(found[kind]) for kind in KINDS},
        **{kind: found[kind][:MAX_LISTED] for kind in KINDS},
    }


def record_changes(snapshot):
    """The catalog changes against the snapshot's base, or None.

    None for a first load (no base), for a delta, for an unchanged catalog,
    and on any failure: like the changed-record counts, this is display-only
    and must never fail a refresh. A delta copies its base's catalog, so it
    can never differ; skipping it keeps two catalog reads off the global work
    lock every 15 minutes. A failure (confined to a savepoint so the caller's
    transaction stays usable) logs a classified WARNING with no exception
    text, naming this comparison so it is told apart from the change counts.
    """
    if snapshot.base_id is None or snapshot.kind != "full":
        return None
    try:
        with transaction.atomic():
            return catalog_changes(
                catalog_names(snapshot.base_id), catalog_names(snapshot.pk)
            )
    except Exception as error:
        emit_failure(
            error,
            event=Event.REPORT_SHAPING_FAILED,
            level=logging.WARNING,
            task_id=snapshot.task_id,
            shaping="ministry_catalog",
        )
        return None


def _entries(record, kind, selected):
    """One kind's listed entries, marked when the current campaign uses them."""
    rows = []
    for row in record.get(kind) or []:
        entry = {
            "duid": row["duid"],
            "name": row["name"],
            "in_campaign": row["duid"] in selected,
        }
        if kind == "renamed":
            entry["before"] = row["before"]
            entry["retired"] = retired_name(row["name"]) and not retired_name(
                row["before"]
            )
        rows.append(entry)
    return rows


# The home page's one read for the notice: the recorded changes of the most
# recent promoted snapshots in the window, and the current catalog's rows for
# the open campaign's selected DUIDs (none when the array is empty). The page
# has a fixed query budget, so both ride on one statement; each row says which
# part it came from. Both parts use only columns the web role already reads.
NOTICE_SQL = f"""
SELECT 'refresh', recent.promoted_at, recent.generation, recent.record, NULL
FROM (
    SELECT promoted_at, generation, (cursor->'{CURSOR_KEY}')::text AS record
    FROM {SourceSnapshot._meta.db_table}
    WHERE state = 'promoted' AND promoted_at >= %s AND cursor ? '{CURSOR_KEY}'
    ORDER BY promoted_at DESC, generation DESC
    LIMIT {MAX_REFRESHES}
) AS recent
UNION ALL
SELECT 'ministry', NULL, NULL, membership.source_key, payload.canonical
FROM {SnapshotMinistry._meta.db_table} AS membership
JOIN {SourceMinistry._meta.db_table} AS payload ON payload.id = membership.payload_id
WHERE membership.snapshot_id = (
    SELECT snapshot_id FROM {SourceCurrent._meta.db_table}
) AND membership.source_key = ANY(%s)
"""


def _read(now, selected):
    """Run the notice's one query; return recent records and present names.

    Records are ``(promoted_at, generation, record)`` newest first; names
    map each selected DUID still in the current catalog to its display name.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            NOTICE_SQL, [now - WINDOW, [str(duid) for duid in sorted(selected)]]
        )
        rows = cursor.fetchall()
    records = sorted(
        (
            (promoted_at, generation, json.loads(text))
            for part, promoted_at, generation, text, _ in rows
            if part == "refresh"
        ),
        key=lambda row: row[:2],
        reverse=True,
    )
    present = {
        int(key): _name(int(key), json.loads(text).get("name"))
        for part, _, _, key, text in rows
        if part == "ministry"
    }
    return records, present


def _refreshes(records, selected):
    """Shape recorded changes for the page, marking campaign Ministries."""
    refreshes = []
    for promoted_at, _, record in records:
        counts = record.get("counts", {})
        refresh = {"promoted_at": promoted_at, "more": {}}
        for kind in KINDS:
            refresh[kind] = _entries(record, kind, selected)
            refresh["more"][kind] = max(0, counts.get(kind, 0) - len(refresh[kind]))
        refreshes.append(refresh)
    return refreshes


def _campaign_attention(selected, present, removed_names):
    """The campaign's selected Ministries now missing or named as retired.

    ``present`` maps the selected DUIDs still in the current catalog to their
    names. A missing Ministry's last known name comes from a recent removal
    record when there is one, else the "Ministry <DUID>" fallback.
    """
    missing = [
        {"duid": duid, "name": removed_names.get(duid) or _name(duid, None)}
        for duid in sorted(selected - present.keys())
    ]
    retired = [
        {"duid": duid, "name": name}
        for duid, name in sorted(present.items())
        if retired_name(name)
    ]
    return missing, retired


def selected_ministries(configuration, campaign):
    """The current campaign's selected Ministry DUIDs while it is still open.

    Read from the applied configuration document the dashboard already holds,
    so it costs no query. Empty without a current, open campaign.
    """
    if campaign is None or campaign.state not in OPEN_STATES:
        return frozenset()
    document = configuration.active_configuration.canonical_document
    for row in document["sections"].get("campaigns", []):
        if row["id"] == str(configuration.current_campaign_id):
            return frozenset(row["values"].get("ministry_duids", []))
    return frozenset()


def catalog_notice(configuration, campaign, now, *, promoted):
    """The Admin home page's Ministry catalog notice, or None when there is none.

    ``promoted`` says whether a source snapshot is current; without one there
    is no catalog to compare with. Costs exactly one query.

    Entries are marked "in the current campaign", and the campaign's missing
    or "X-" Ministries listed, only while that campaign is draft, scheduled or
    open (``selected_ministries``); a closed campaign shows Families nothing.
    """
    if not promoted:
        return None
    selected = selected_ministries(configuration, campaign)
    records, present = _read(now, selected)
    refreshes = _refreshes(records, selected)
    removed_names = {
        row["duid"]: row["name"]
        for refresh in reversed(refreshes)
        for row in refresh["removed"]
    }
    missing, retired = _campaign_attention(selected, present, removed_names)
    if not (refreshes or missing or retired):
        return None
    return {
        "refreshes": refreshes,
        "missing": missing,
        "retired": retired,
        "campaign_id": campaign.pk if selected else None,
    }
