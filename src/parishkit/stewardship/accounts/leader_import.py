"""ParishSoft Ministry leaders without a user, and their import (#952 slice 6).

Below its users table, the Users page lists the people ParishSoft counts as
Ministry leaders who have no user yet (admin-portal specification,
"ParishSoft Ministry leaders without a user"), and imports the ones the
Administrator chooses as Ministry leader users. ``pk-admin users
import-leaders`` runs the same code:

- ``read_leaders`` reads the rows through the
  ``stewardship_leaders_without_user_v1`` definer (migration 0054), which
  answers only a live Administrator session, takes no address and records
  its own count-only audit event. The web login reads no Member, contact or
  roster record itself.
- ``plan_import`` decides, without I/O, which chosen addresses become users
  and which are skipped, and builds the change through
  ``user_changes.plan_changes``, so every import gets the page's checks.
  Every new user is a Ministry leader, existing users are never touched,
  and the last-Administrator guard is unaffected.

Names and addresses come only from the definer's answer, never from what a
form posts: a posted address only chooses among the rows read now.
"""

import json
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from parishkit.config import ConfigError
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.source.catalog_names import ministry_display_name
from parishkit.stewardship.web.tables import Sorting

from .policy_schema import normalized_email
from .user_changes import ENTRY_LIMIT, LOOKUP_REFUSED, plan_changes, saved_users
from .users_upgrade import default_name

# Every imported user's role (Administrator decision, 2026-10-10): import
# never grants more than Ministry leader.
IMPORT_ROLE = "ministry_leader"
# Ministries a table cell names before "and N more".
SHOWN_MINISTRIES = 2

# The leaders table's sort (admin-portal "ParishSoft Ministry leaders without
# a user"): Name by default, then Member DUID or Email.
LEADER_SORTING = Sorting.by_column(
    {
        "name": lambda row: row["name"].casefold(),
        "member_duid": lambda row: row["member_duid"],
        "email": lambda row: row["email"],
    },
    default="name",
)

SKIP_REASONS = {
    "user": _("Already a user"),
    "not_listed": _("No longer a ParishSoft Ministry leader without a user"),
}


class LeadersRefused(PermissionError):
    """The definer refused the session: not a live Administrator's."""


# How the definer words its session refusal (SQLSTATE 42501, which a login
# without EXECUTE also gets, so the words tell the two apart).
REFUSAL = "Leader list requires a live Administrator session"


@dataclass(frozen=True)
class Leaders:
    """The leaders table's rows and the leaders it cannot list.

    Each row is ``{"email", "name", "member_duid", "ministries"}``, where
    ``ministries`` holds the ascending DUIDs of the Ministries the address's
    leader Members lead. ``unreachable`` counts leader Members whose
    ParishSoft contact record lists no valid address, who cannot sign in.
    """

    rows: list
    unreachable: int

    def by_email(self):
        """The rows keyed by address."""
        return {row["email"]: row for row in self.rows}


def _row(item):
    """One definer row, as the users schema would accept it, or None.

    An address the users schema refuses cannot become a user, so it is left
    out; a Name it refuses (empty, too long or with controls) falls back to
    the address's local part, as the users upgrade's default does.
    """
    try:
        email = normalized_email(item["email"].strip())
    except (AttributeError, ConfigError, KeyError, TypeError):
        return None
    name = item.get("name")
    return {
        "email": email,
        "name": default_name(email, [name] if isinstance(name, str) else []),
        "member_duid": item.get("member_duid"),
        "ministries": [duid for duid in item.get("ministries") or [] if duid],
    }


def read_leaders(session_key):
    """The ParishSoft Ministry leaders without a user, for this Admin session.

    Calls the definer in its own savepoint (it writes its audit event) and
    turns its JSON answer into ``Leaders``. A session the definer refuses
    raises ``LeadersRefused``; any other database failure, a login without
    EXECUTE included, propagates.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.stewardship_leaders_without_user_v1(%s, %s)::text",
                [session_key, current_correlation()],
            )
            (value,) = cursor.fetchone()
    except DatabaseError as error:
        cause = error.__cause__
        if getattr(cause, "sqlstate", None) == LOOKUP_REFUSED and (
            getattr(getattr(cause, "diag", None), "message_primary", None) == REFUSAL
        ):
            raise LeadersRefused("Leader list refused.") from None
        raise
    answer = json.loads(value)
    rows, seen = [], set()
    for item in answer.get("leaders", []):
        row = _row(item)
        if row is not None and row["email"] not in seen:
            seen.add(row["email"])
            rows.append(row)
    return Leaders(rows=rows, unreachable=int(answer.get("unreachable", 0)))


def ministries_label(names):
    """A short list of Ministry names, such as "Choir, Ushers and 3 more"."""
    shown = ", ".join(names[:SHOWN_MINISTRIES])
    rest = len(names) - SHOWN_MINISTRIES
    if rest <= 0:
        return shown
    return ngettext(
        "%(shown)s and %(count)d more", "%(shown)s and %(count)d more", rest
    ) % {"shown": shown, "count": rest}


def ministry_names(duids):
    """Each Ministry DUID's display name in the current promoted catalog.

    Read from the snapshot tables the web login already reads; a DUID the
    catalog no longer has gets the shared "Ministry <DUID>" fallback.
    """
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.version_models import SnapshotMinistry

    wanted = {int(duid) for duid in duids}
    current = SourceCurrent.objects.filter(singleton=True).first()
    names = {}
    if wanted and current is not None and current.snapshot_id is not None:
        for row in SnapshotMinistry.objects.filter(
            snapshot_id=current.snapshot_id,
            source_key__in=[str(duid) for duid in wanted],
        ).select_related("payload"):
            names[int(row.source_key)] = ministry_display_name(
                int(row.source_key), row.payload.payload.get("name")
            )
    return {
        duid: names.get(duid) or ministry_display_name(duid, None) for duid in wanted
    }


def table_rows(leaders):
    """The leaders' rows with their Ministries named, for the page and CLI.

    ``ministry_names`` lists every name in order (the cell's tooltip and the
    command line); ``ministries_label`` is the cell's short form.
    """
    names = ministry_names({duid for row in leaders.rows for duid in row["ministries"]})
    shaped = []
    for row in leaders.rows:
        listed = sorted(
            (names[duid] for duid in row["ministries"]),
            key=lambda name: name.casefold(),
        )
        shaped.append(
            row
            | {"ministry_names": listed, "ministries_label": ministries_label(listed)}
        )
    return shaped


@dataclass(frozen=True)
class ImportPlan:
    """What one import does: the change, and who is left out and why.

    ``changes`` and ``patch`` are ``plan_changes``' result for the users it
    adds (empty when nobody is left to import); ``added`` lists their rows
    in Name order; ``skipped`` lists ``{"email", "reason"}`` for each chosen
    address that is now a user (``user``) or no longer listed
    (``not_listed``); ``remaining`` counts chosen leaders past the
    ``ENTRY_LIMIT`` users one change may add, left for a later import.
    """

    changes: list
    patch: list
    added: list
    skipped: list
    remaining: int


def plan_import(records, leaders, chosen=None):
    """Plan importing the chosen leaders (all of them when ``chosen`` is None).

    ``records`` are the applied ``login_rules``; ``leaders`` is
    ``read_leaders``' answer; ``chosen`` is the addresses the Administrator
    ticked, already trimmed and lowercased. Each added user takes the row's
    default Name and the Ministry leader role. An address that is a user in
    ``records`` is skipped as ``user`` even when the leaders were read a
    moment earlier, so an import never changes an existing user.
    """
    users = {values["email"] for values in saved_users(records).values()}
    listed = leaders.by_email()
    wanted = [row["email"] for row in leaders.rows] if chosen is None else chosen
    added, skipped = [], []
    for email in dict.fromkeys(wanted):
        if email in users:
            skipped.append({"email": email, "reason": "user"})
        elif email in listed:
            added.append(listed[email])
        else:
            skipped.append({"email": email, "reason": "not_listed"})
    added.sort(key=lambda row: (row["name"].casefold(), row["email"]))
    remaining = max(len(added) - ENTRY_LIMIT, 0)
    added = added[:ENTRY_LIMIT]
    if not added:
        return ImportPlan([], [], [], skipped, 0)
    changes, patch = plan_changes(
        records,
        [
            {"email": row["email"], "name": row["name"], "role": IMPORT_ROLE}
            for row in added
        ],
    )
    return ImportPlan(changes, patch, added, skipped, remaining)


def imported_ids(patch):
    """The record IDs of the users an import's patch adds, in patch order."""
    return [
        operation["id"]
        for operation in patch
        if operation["operation"] == "add" and operation["section"] == "login_rules"
    ]
