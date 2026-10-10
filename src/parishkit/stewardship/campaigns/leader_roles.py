"""A campaign's Ministry leader roles (#922), as Campaign settings shows them.

People holding any of a campaign's leader roles in a Ministry, in the promoted
ParishSoft rosters, lead that Ministry. Who that is lives only in SQL
(``stewardship_ministry_leaders_v1``), and so does the default role list
(``stewardship_ministry_leader_roles_v1``): this module reads both through
SQL rather than restating them, and offers the role labels the current
rosters use as choices.
"""

import json

from django.db import connection

from .configuration import MINISTRY_LEADER_ROLES, leader_role_key


def effective_roles(values):
    """The campaign's leader role names: its saved list, or SQL's default."""
    saved = (
        {MINISTRY_LEADER_ROLES: values[MINISTRY_LEADER_ROLES]}
        if MINISTRY_LEADER_ROLES in values
        else {}
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_ministry_leader_roles_v1(%s::jsonb)",
            [json.dumps(saved)],
        )
        value = cursor.fetchone()[0]
    return list(json.loads(value) if isinstance(value, str) else value)


def roster_role_names():
    """Distinct role labels on the promoted source's current roster rows.

    Labels are trimmed; ones that differ only in ASCII case count once (SQL
    matches them alike), spelled as the first in sorted order. No current
    source means none.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT btrim(roster.canonical::jsonb->>'ministryRoleName') "
            "FROM stewardship_source_current pointer "
            "JOIN stewardship_snapshot_roster rm ON rm.snapshot_id=pointer.snapshot_id "
            "JOIN stewardship_source_roster roster ON roster.id=rm.payload_id "
            "WHERE roster.canonical::jsonb->'current'='true'::jsonb "
            "AND jsonb_typeof(roster.canonical::jsonb->'ministryRoleName')='string'"
        )
        names = sorted(row[0] for row in cursor.fetchall() if row[0])
    unique = {}
    for name in names:
        unique.setdefault(leader_role_key(name), name)
    return list(unique.values())


def role_choices(selected):
    """Form choices: the selected names, then every other current roster label.

    A saved name stays selectable even when no roster uses it now, and keeps
    its saved spelling. The list is sorted case-insensitively.
    """
    unique = {leader_role_key(name): name for name in selected}
    for name in roster_role_names():
        unique.setdefault(leader_role_key(name), name)
    return [
        (name, name)
        for name in sorted(unique.values(), key=lambda name: (name.casefold(), name))
    ]


def canonical_roles(names):
    """The stored form of a chosen list: sorted case-insensitively, as shown."""
    return sorted(names, key=lambda name: (name.casefold(), name))
