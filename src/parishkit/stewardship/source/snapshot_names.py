"""Family names ("Squyres, Tracy and Jeff") read from one source snapshot.

Admin pages that list Families by DUID (the chosen-Families send page, the
"Families on the form now" page) name them the way the Family codes directory
does (``family_names.family_heads_name``): the surname, then the active heads
of household. Two queries serve any number of Families.
"""

from .family_names import family_display_name, family_heads_name
from .version_models import SnapshotFamily, SnapshotMember


def snapshot_family_names(snapshot_id, duids, default=""):
    """Map each requested Family DUID (int) in the snapshot to its display name.

    Heads come from the Family's ``active_head_duids`` in DUID order, and only
    active Members count, matching the directory SQL. A DUID missing from the
    snapshot is left out; ``default`` names a Family without a surname.
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
        names[duid] = family_heads_name(family_display_name(values, default), heads)
    return names
