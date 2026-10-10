"""Family names ("Squyres, Tracy and Jeff") read from one source snapshot.

Admin pages that list Families by DUID (the chosen-Families send page, the
"Families on the form now" page, and the reports whose SQL reads only a
surname, through ``name_rows``) name them the way the Family codes directory
does (``family_names.family_heads_name``): the surname, then the active heads
of household. One statement serves any number of Families. The response lists
also show each Family's envelope number and ParishSoft mailing name
(``snapshot_family_facts``), read from the same Family rows.

One SQL statement reads the snapshot's state, its Family rows and their heads'
Member rows together. Compaction marks a snapshot compacted and commits before
it deletes any of that snapshot's rows, so a statement that sees the snapshot
uncompacted also sees every one of its rows, and one that sees it compacted
names no Family at all. A caller therefore never mixes full names with
partially reclaimed ones (#932).
"""

import json
from dataclasses import dataclass

from django.db import connection

from .family_names import family_display_name, family_heads_name


@dataclass(frozen=True)
class FamilyFacts:
    """What the Admin lists show of one snapshot Family besides its DUID.

    ``name`` is the directory's surname-and-heads name; ``envelope`` the
    ParishSoft envelope number (None when the record has none);
    ``mailing_name`` the ParishSoft mailing name, stripped ("" when blank).
    """

    name: str
    envelope: int | None
    mailing_name: str


def snapshot_family_names(snapshot_id, duids, default=""):
    """Map each requested Family DUID (int) in the snapshot to its display name.

    Heads come from the Family's ``active_head_duids`` in DUID order, and only
    active Members count, matching the directory SQL. A DUID missing from the
    snapshot is left out; ``default`` names a Family without a surname.
    """
    facts = snapshot_family_facts(snapshot_id, duids, default)
    return {duid: fact.name for duid, fact in facts.items()}


def name_rows(snapshot_id, rows, default="Unavailable Family"):
    """Rename each row's ``family_name`` to the directory's surname-and-heads name.

    For report rows whose SQL read gives only the Family's surname, so every
    Admin table names a Family the same way (#932). ``rows`` are dicts with
    ``family_duid`` and ``family_name``, renamed in place. A Family missing
    from the snapshot keeps the name it was read with.

    Returns True when the rows were renamed, or False when the snapshot has
    been compacted (or is not a promoted snapshot) and every row keeps the
    surname it was read with. The choice is made once for all ``rows``, so
    one table or file never mixes the two forms.
    """
    facts = _read_facts(snapshot_id, {row["family_duid"] for row in rows}, default)
    if facts is None:
        return False
    for row in rows:
        fact = facts.get(row["family_duid"])
        if fact is not None:
            row["family_name"] = fact.name
    return True


def _envelope(value):
    """The envelope number when the record holds a whole number, else None."""
    return value if type(value) is int else None


def snapshot_family_facts(snapshot_id, duids, default=""):
    """Map each requested Family DUID (int) in the snapshot to its ``FamilyFacts``.

    The name follows ``snapshot_family_names``; a DUID missing from the
    snapshot is left out, and a compacted snapshot names no Family.
    """
    if not duids:
        return {}
    return _read_facts(snapshot_id, duids, default) or {}


# The snapshot's state, the requested Family rows and their heads' Member rows,
# read by one statement so they all come from one MVCC view (see the module
# docstring). Payloads are returned as their stored canonical JSON text, the
# same text ``SourceVersion.payload`` decodes. A head list that is not an array
# names no heads rather than failing the read.
_FACTS_SQL = """
WITH snapshot AS (
    SELECT id FROM stewardship_source_snapshot
    WHERE id=%(snapshot)s AND state='promoted' AND compacted_at IS NULL
), families AS (
    SELECT f.source_key, p.canonical
    FROM stewardship_snapshot_family f
    JOIN stewardship_source_family p ON p.id=f.payload_id
    WHERE f.snapshot_id=(SELECT id FROM snapshot) AND f.source_key=ANY(%(keys)s)
), heads AS (
    SELECT m.source_key, p.canonical
    FROM stewardship_snapshot_member m
    JOIN stewardship_source_member p ON p.id=m.payload_id
    WHERE m.snapshot_id=(SELECT id FROM snapshot)
      AND m.source_key IN (
        SELECT jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(f.canonical::jsonb->'active_head_duids')='array'
            THEN f.canonical::jsonb->'active_head_duids' ELSE '[]'::jsonb END
        )
        FROM families f
      )
)
SELECT EXISTS (SELECT 1 FROM snapshot),
    (SELECT json_object_agg(source_key, canonical::json) FROM families)::text,
    (SELECT json_object_agg(source_key, canonical::json) FROM heads)::text
"""


def _read_facts(snapshot_id, duids, default):
    """Read and name the requested Families, or None if the snapshot is unusable.

    None means the snapshot is missing, not promoted, or compacted (even if
    its rows are only partly reclaimed so far); otherwise the result maps each
    requested Family DUID found in the snapshot to its ``FamilyFacts``.
    """
    if snapshot_id is None:
        return None
    payloads = _read_payloads(snapshot_id, sorted(str(duid) for duid in duids))
    if payloads is None:
        return None
    families = {int(key): value for key, value in payloads[0].items()}
    members = payloads[1]
    names = {}
    for duid, values in families.items():
        heads = []
        for head in sorted(values.get("active_head_duids") or (), key=int):
            member = members.get(str(head))
            if member is None or member.get("active") is not True:
                continue
            first = (member.get("firstName") or "").strip()
            last = (member.get("lastName") or "").strip()
            heads.append(
                {"first": first, "last": last, "name": f"{first} {last}".strip()}
            )
        mailing = values.get("mailingName")
        names[duid] = FamilyFacts(
            family_heads_name(family_display_name(values, default), heads),
            _envelope(values.get("envelopeNumber")),
            mailing.strip() if isinstance(mailing, str) else "",
        )
    return names


def _read_payloads(snapshot_id, keys):
    """Run ``_FACTS_SQL``: (Family payloads, head Member payloads) by key, or None.

    None when the snapshot is missing, not promoted or compacted.
    """
    with connection.cursor() as cursor:
        cursor.execute(_FACTS_SQL, {"snapshot": snapshot_id, "keys": keys})
        usable, family_text, head_text = cursor.fetchone()
    if not usable:
        return None
    return json.loads(family_text or "{}"), json.loads(head_text or "{}")


# The "Family names" report detail of a file whose Families ``name_rows``
# named: which of the two forms the whole file uses (#932), the way the
# directory export states "Head emails as of" when it fell back.
FAMILY_NAMES_DETAIL = "Family names"
FAMILY_NAMES_FULL = "Surname, then heads of household"
FAMILY_NAMES_SURNAME = (
    "Surname only: the ParishSoft data this file was captured from has since "
    "been reclaimed"
)


def name_file_rows(metadata, rows, default="Unavailable Family"):
    """Name a file's rows with ``name_rows`` and record which form it used.

    Sets ``metadata["family_names"]`` to the "Family names" detail text, so
    the file states whether every Family is named by surname and heads or,
    when the capture's snapshot has been compacted, by surname alone.
    """
    full = name_rows(metadata["source_id"], rows, default)
    metadata["family_names"] = FAMILY_NAMES_FULL if full else FAMILY_NAMES_SURNAME
