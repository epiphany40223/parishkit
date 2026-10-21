"""Who a person is in ParishSoft, and who a Family's users are (#1867).

A user's Timeline (``user_timelines``) shows the ParishSoft Family the
user's address belongs to, and a Family's Timeline (``reports.timeline_views``)
names the Family's users. Both directions match an address against Family
and Member contact records, which the web login may not read, so each goes
through one Administrator-only definer read of frozen migration 0157 (data
spec, "Person identity reads") that returns identifiers only:

- ``address_people``: ``stewardship_address_people_v1``, the Families (and
  Members) whose contacts hold the address;
- ``family_users``: ``stewardship_family_users_v1``, the record ids of the
  current users whose address is a contact of the Family or its Members.

Each raises ``PermissionError`` when the definer refuses the reader (it
checks the actor is an enabled Administrator itself), so a page answers
with its denial rather than a database error.
"""

from parishkit.stewardship.jobs.delivery_reads import _definer_rows


def address_people(address, actor_id):
    """``{family DUID: (Member DUIDs, ...)}`` for the address, in DUID order.

    A Family whose own contact holds the address has an empty Member tuple
    unless one of its Members' contacts holds it too. Nothing before the
    first ParishSoft snapshot.
    """
    rows = _definer_rows(
        "SELECT family_duid, member_duid"
        " FROM public.stewardship_address_people_v1(%s,%s)",
        (address, actor_id),
    )
    if rows is None:
        raise PermissionError("People require an Administrator.")
    families = {}
    for family, member in rows:
        members = families.setdefault(family, [])
        if member is not None:
            members.append(member)
    return {family: tuple(members) for family, members in sorted(families.items())}


def family_users(family_duid, actor_id):
    """The record ids of the current users who belong to the Family."""
    rows = _definer_rows(
        "SELECT * FROM public.stewardship_family_users_v1(%s,%s)",
        (family_duid, actor_id),
    )
    if rows is None:
        raise PermissionError("People require an Administrator.")
    return [row[0] for row in rows]


def family_user_rows(family_duid, actor_id):
    """The Family's current users, for its Timeline: ``[{id, display, role}]``.

    ``family_users`` names them; their Name, address and role are read from
    the active configuration's user rows (one query, none without users),
    ordered by the shown name. ``display`` is the Name, or the address when
    the Name is blank, as the user's Timeline heading reads.
    """
    from django.db.models import Subquery

    from .policy_models import AddressRule
    from .runtime_models import SystemConfiguration
    from .user_rows import ROLE_LABELS

    ids = family_users(family_duid, actor_id)
    if not ids:
        return []
    active = SystemConfiguration.objects.values("active_configuration_id")[:1]
    rows = AddressRule.objects.filter(
        configuration_id=Subquery(active), record_id__in=ids, role__isnull=False
    ).values("record_id", "email", "name", "role")
    users = [
        {
            "id": row["record_id"],
            "display": row["name"] or row["email"],
            "role": ROLE_LABELS.get(row["role"], ""),
        }
        for row in rows
    ]
    return sorted(users, key=lambda each: (each["display"].casefold(), each["id"]))
