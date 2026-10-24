"""A small parish whose Families cover every Families-rule case (#1393).

Shared by the pure tests of the rule, the corpus and the Families check.
"""

from parishkit.parishsoft import ParishSoftData

ORGANIZATION = 5
GROUPS = {1: "(blank)", 7: "Active", 8: "Staff", 9: "Inactive", 10: "Other"}


def _family(duid, *, group=None, registered=ORGANIZATION):
    """One Family's search row; ``group`` is its famGroupID (0: no group)."""
    row = {"familyDUID": duid, "lastName": f"Family {duid}"}
    if registered is not None:
        row["registeredOrganizationID"] = registered
    if group is not None:
        row["famGroupID"] = group
    return row


def _member(duid, family, status, *, member_type="Head", email=None):
    """One Member; heads with ``email`` can be emailed."""
    row = {
        "memberDUID": duid,
        "familyDUID": family,
        "firstName": f"Member {duid}",
        "lastName": "Example",
        "memberType": member_type,
        "memberStatus": status,
    }
    if email is not None:
        row["emailAddress"] = email
    return row


# Each Family: (group, registered organization, [(member status, type, email)]).
FAMILIES = {
    # Active group, registered, an Active head with email: every rule but a
    # no-group Advanced choice selects it.
    101: (7, ORGANIZATION, [("Active", "Head", "a101@example.org")]),
    # Staff group: the second preset and legacy select it, the first not.
    102: (8, ORGANIZATION, [("Active", "Head", "a102@example.org")]),
    # Inactive group: legacy never selects it; Advanced may.
    103: (9, ORGANIZATION, [("Active", "Head", "a103@example.org")]),
    # No group (famGroupID 0) and no famGroupID at all.
    104: (0, ORGANIZATION, [("Active", "Head", "a104@example.org")]),
    105: (None, ORGANIZATION, [("Active", "Head", "a105@example.org")]),
    # Registered elsewhere, and not registered at all.
    106: (7, 6, [("Active", "Head", "a106@example.org")]),
    107: (7, None, [("Active", "Head", "a107@example.org")]),
    # Only an ActOther head: legacy selects it, presets do not.
    108: (7, ORGANIZATION, [("ActOther", "Head", "a108@example.org")]),
    # An Active child but an ActOther head with the only email: a preset
    # selects it but cannot email it; legacy emails the head.
    109: (
        7,
        ORGANIZATION,
        [("ActOther", "Head", "a109@example.org"), ("Active", "Child", None)],
    ),
    # Inactive and Deceased Members only: no rule selects it.
    110: (7, ORGANIZATION, [("Inactive", "Head", "a110@example.org")]),
    111: (7, ORGANIZATION, [("Deceased", "Head", "a111@example.org")]),
    # An Unknown head and an empty status.
    112: (10, ORGANIZATION, [("Unknown", "Head", "a112@example.org")]),
    113: (1, ORGANIZATION, [("", "Wife", "a113@example.org")]),
    # Active head without email, Deceased spouse with one.
    114: (
        7,
        ORGANIZATION,
        [("Active", "Head", None), ("Deceased", "Wife", "a114@example.org")],
    ),
    # No Members at all.
    115: (7, ORGANIZATION, []),
}


def parish():
    """The shared loader's plain collections for ``FAMILIES``."""
    families, members = {}, {}
    duid = 1000
    for family, (group, registered, people) in FAMILIES.items():
        families[family] = _family(family, group=group, registered=registered)
        for status, member_type, email in people:
            duid += 1
            members[duid] = _member(
                duid, family, status, member_type=member_type, email=email
            )
    return ParishSoftData(
        organization_id=ORGANIZATION,
        families=families,
        members=members,
        family_groups=dict(GROUPS),
        family_workgroups={},
        family_workgroup_memberships={},
        member_workgroups={},
        member_workgroup_memberships={},
        member_contactinfos={},
        ministry_types={},
        ministry_type_memberships={},
        funds={},
        pledges={},
        contributions={},
    )
