"""Users-page changes as minimal configuration patches, decided by pure code.

The Users page (#952 slice 5) and ``pk-admin users preview`` both describe a
change as a list of entries, the command line's change document:

- ``{"email", "name", "role"}`` adds a user (``role`` defaults to Ministry
  leader; ``name`` may be left out only where a default is supplied);
- ``{"id", "name"?, "role"?}`` changes a saved user's Name or role;
- ``{"id", "delete": true}`` deletes a saved user.

``plan_changes`` turns the entries into one ``login_rules`` patch with each
user's Name and role before and after, or refuses with a ``UserRefused``
naming the field and a plain message, never repeating what was submitted.
It enforces what the page promises before anything is signed: one valid,
new address per added user (changing an address is Delete and then New), a
1-200 character Name, one of the three roles, a real change, and the
last-Administrator guard. The whole candidate is still validated by the
``users-v10`` schema at intake and again by SQL at activation, so this is
the early, readable refusal, not the only one.
"""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError

from .policy_schema import normalized_email
from .users_schema import NAME_LIMIT, ROLE_LEVELS, valid_name

# A new user's role unless another is chosen (Administrator decision,
# 2026-10-10): the least access that still lets the person sign in.
DEFAULT_ROLE = "ministry_leader"
# A change document holds at most this many entries: one request's patch
# limit (``configuration_requests.record_request``).
ENTRY_LIMIT = 100
ADD_FIELDS = {"email", "name", "role"}
UPDATE_FIELDS = {"id", "name", "role"}

MESSAGES = {
    "email_required": _("Enter the email address this person signs in with."),
    "email_invalid": _("Enter one email address, such as name@example.org."),
    "email_taken": _(
        "This address is already a user. Choose Edit on that user's row instead."
    ),
    "email_repeated": _("This address is added more than once."),
    "name_required": _("Enter a name."),
    "name_invalid": _(
        "Use at most %(limit)s characters, on one line, with no special "
        "control characters."
    )
    % {"limit": NAME_LIMIT},
    "role_invalid": _("Choose Ministry leader, Staff or Administrator."),
    "missing": _("That user no longer exists. Return to Users to see the list."),
    "repeated": _("The same user is changed more than once."),
    "unchanged": _("Nothing has changed. Change the Name or the role, then review."),
    "last_administrator": _(
        "At least one Administrator must remain. Make another user an "
        "Administrator first."
    ),
    "invalid": _("This change can't be made. Return to Users and start again."),
}


class UserRefused(ValueError):
    """A change the builder will not make: a field, a closed code and words.

    ``field`` is ``email``, ``name``, ``role`` or None for the change as a
    whole; ``index`` is the entry's place in the change document. The
    message is one of ``MESSAGES``, never built from submitted values.
    """

    def __init__(self, code, field=None, index=0):
        """Carry the code, the field it concerns and the entry's index."""
        super().__init__(code)
        self.code = code
        self.field = field
        self.index = index

    @property
    def message(self):
        """The plain explanation the page and the command line show."""
        return MESSAGES[self.code]


@dataclass(frozen=True)
class UserChange:
    """One user's change: its ``operation`` and Name and role before and after.

    ``before`` and ``after`` are ``{"name", "role"}`` or None (added or
    deleted); ``id`` is the user's opaque record ID; ``email`` never changes.
    """

    operation: str
    id: str
    email: str
    before: dict | None
    after: dict | None

    @property
    def grants_administrator(self):
        """True when this change makes someone an Administrator (an alert)."""
        return (self.after or {}).get("role") == "administrator" and (
            self.before or {}
        ).get("role") != "administrator"

    @property
    def removes_administrator(self):
        """True when an Administrator is demoted or deleted (high impact)."""
        return (self.before or {}).get("role") == "administrator" and (
            self.after or {}
        ).get("role") != "administrator"

    @property
    def changed(self):
        """The fields that differ, in the page's order (``name``, ``role``)."""
        before, after = self.before or {}, self.after or {}
        return [
            field for field in ("name", "role") if before.get(field) != after.get(field)
        ]


def saved_users(records):
    """The users of a ``login_rules`` list, by record ID, in address order."""
    users = {
        record["id"]: record["values"]
        for record in records
        if record["values"].get("kind") == "user"
    }
    return dict(sorted(users.items(), key=lambda item: item[1]["email"]))


def administrator_ids(users):
    """The IDs of the users who are Administrators."""
    return {key for key, values in users.items() if values["role"] == "administrator"}


def clean_email(value, index=0):
    """A typed address, trimmed and lowercased as sign-in matches, or refuse."""
    if value is None or (type(value) is str and not value.strip()):
        raise UserRefused("email_required", "email", index)
    try:
        return normalized_email(value.strip() if type(value) is str else value)
    except ConfigError:
        raise UserRefused("email_invalid", "email", index) from None


def clean_name(value, index=0):
    """A typed Name, trimmed, 1-200 characters with no controls, or refuse."""
    if value is None or (type(value) is str and not value.strip()):
        raise UserRefused("name_required", "name", index)
    try:
        return valid_name(value.strip() if type(value) is str else value)
    except ConfigError:
        raise UserRefused("name_invalid", "name", index) from None


def clean_role(value, index=0):
    """One of the three roles, or refuse."""
    if value not in ROLE_LEVELS:
        raise UserRefused("role_invalid", "role", index)
    return value


def _record_id(value, index):
    """A saved user's ID as the patch names it: a canonical UUID string."""
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise UserRefused("missing", None, index) from None
    return value


def _added(entry, index, users, taken, default_name):
    """One added user's change and patch operation."""
    if set(entry) - ADD_FIELDS:
        raise UserRefused("invalid", None, index)
    email = clean_email(entry.get("email"), index)
    if email in {values["email"] for values in users.values()}:
        raise UserRefused("email_taken", "email", index)
    if email in taken:
        raise UserRefused("email_repeated", "email", index)
    taken.add(email)
    name = entry.get("name")
    if name is None and default_name is not None:
        name = default_name(email)
    name = clean_name(name, index)
    role = clean_role(entry.get("role", DEFAULT_ROLE), index)
    identifier = str(uuid4())
    values = {"kind": "user", "email": email, "name": name, "role": role}
    change = UserChange("add", identifier, email, None, {"name": name, "role": role})
    return change, {
        "operation": "add",
        "section": "login_rules",
        "id": identifier,
        "values": values,
    }


def _updated(entry, index, users):
    """One saved user's Name or role change, or deletion, and its operation."""
    identifier = _record_id(entry.get("id"), index)
    if identifier not in users:
        raise UserRefused("missing", None, index)
    saved = users[identifier]
    before = {"name": saved["name"], "role": saved["role"]}
    if entry.get("delete") is not None:
        if entry != {"id": identifier, "delete": True}:
            raise UserRefused("invalid", None, index)
        change = UserChange("remove", identifier, saved["email"], before, None)
        return change, {
            "operation": "remove",
            "section": "login_rules",
            "id": identifier,
        }
    if set(entry) - UPDATE_FIELDS:
        raise UserRefused("invalid", None, index)
    after = {
        "name": clean_name(entry["name"], index) if "name" in entry else before["name"],
        "role": clean_role(entry["role"], index) if "role" in entry else before["role"],
    }
    change = UserChange("update", identifier, saved["email"], before, after)
    if not change.changed:
        raise UserRefused("unchanged", None, index)
    values = {field: after[field] for field in change.changed}
    return change, {
        "operation": "update",
        "section": "login_rules",
        "id": identifier,
        "values": values,
    }


def plan_changes(records, entries, *, default_name=None):
    """Turn a change document's entries into ``(changes, patch)``, or refuse.

    ``records`` are the applied ``login_rules``; ``default_name(email)``, when
    given, names an added user whose entry has no ``name`` (the command line;
    the page always posts one). Entries are checked in order, so the first
    problem is the one reported. A user may appear once. The result must keep
    at least one Administrator: the last-Administrator guard, which the
    ``users-v10`` schema and SQL activation also enforce.
    """
    if type(entries) is not list or not 1 <= len(entries) <= ENTRY_LIMIT:
        raise UserRefused("invalid")
    users = saved_users(records)
    changes, patch, taken, seen = [], [], set(), set()
    for index, entry in enumerate(entries):
        if type(entry) is not dict:
            raise UserRefused("invalid", None, index)
        if "id" in entry:
            if entry["id"] in seen:
                raise UserRefused("repeated", None, index)
            seen.add(entry["id"])
            change, operation = _updated(entry, index, users)
        else:
            change, operation = _added(entry, index, users, taken, default_name)
        changes.append(change)
        patch.append(operation)
    remaining = administrator_ids(users) - {
        change.id for change in changes if change.removes_administrator
    }
    if not remaining and not any(change.grants_administrator for change in changes):
        raise UserRefused("last_administrator", "role", 0)
    return changes, patch


def only_administrator(users, identifier):
    """True when this saved user is the one Administrator left.

    The Users table then offers no Delete for that row, and Edit user shows
    the lower roles unavailable with the reason (#563); the server refuses
    either change anyway (``plan_changes``).
    """
    return administrator_ids(users) == {identifier}


def member_names(email):
    """The ordered, distinct ParishSoft Member names that list this address.

    Read through the ``stewardship_member_names_for_v1`` definer (migration
    0053), which matches the address trimmed and case-insensitively and
    returns only names, best first: a Member of an active Family, then a head
    of household, then by name and Member DUID. Names the users schema would
    refuse are left out, so the first one is always usable as a default.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_member_names_for_v1(%s)::text", [email]
        )
        (value,) = cursor.fetchone()
    names = []
    for name in json.loads(value or "[]"):
        try:
            names.append(valid_name(name))
        except ConfigError:
            continue
    return names
