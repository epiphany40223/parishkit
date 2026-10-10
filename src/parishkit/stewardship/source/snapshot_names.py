"""Family names ("Squyres, Tracy and Jeff") read from one source snapshot.

Admin pages that list Families by DUID (the chosen-Families send page, the
"Families on the form now" page, and the reports whose SQL reads only a
surname, through ``name_rows``) name them the way the Family codes directory
does (``family_names.family_heads_name``): the surname, then the active heads
of household. Two queries serve any number of Families. The response lists
also show each Family's envelope number and ParishSoft mailing name
(``snapshot_family_facts``), read from the same Family rows.
"""

from dataclasses import dataclass

from .family_names import family_display_name, family_heads_name
from .version_models import SnapshotFamily, SnapshotMember


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
    ``family_duid`` and ``family_name``, renamed in place and returned. A
    Family missing from the snapshot keeps the name it was read with.
    """
    names = snapshot_family_names(
        snapshot_id, {row["family_duid"] for row in rows}, default
    )
    for row in rows:
        row["family_name"] = names.get(row["family_duid"], row["family_name"])
    return rows


def _envelope(value):
    """The envelope number when the record holds a whole number, else None."""
    return value if type(value) is int else None


def snapshot_family_facts(snapshot_id, duids, default=""):
    """Map each requested Family DUID (int) in the snapshot to its ``FamilyFacts``.

    The name follows ``snapshot_family_names``; a DUID missing from the
    snapshot is left out.
    """
    if snapshot_id is None or not duids:
        return {}
    families = {
        int(row.source_key): row.payload.payload
        for row in SnapshotFamily.objects.filter(
            snapshot_id=snapshot_id, source_key__in=[str(duid) for duid in duids]
        ).select_related("payload")
    }
    head_keys = {
        str(head)
        for values in families.values()
        for head in values.get("active_head_duids") or ()
    }
    members = {
        row.source_key: row.payload.payload
        for row in SnapshotMember.objects.filter(
            snapshot_id=snapshot_id, source_key__in=head_keys
        ).select_related("payload")
    }
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
