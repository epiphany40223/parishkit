"""Operator edits to the fake ParishSoft's synthetic parish (#1780).

The fake serves a parish generated from its configuration file, so its data
never changes on its own. To exercise the ParishSoft refresh re-checks
(Confirmed, Entered incorrectly, Not seen, Conflict) on pk-local, the
operator records edits in that same file's optional ``edits`` list: a
Member's or Family's fields, a Ministry roster entry added or removed, and a
Family's pledge set or removed. ``apply_edits`` replays them in order on the
generated parish when the fake starts, then re-derives the copies the
generator derives (contact rows, member counts, roster Family ids), so the
served collections stay mutually consistent. Edits reach the fake through
its file, which the operator script rewrites and then recreates the service
(see "Fake ParishSoft edits" in
docs/specs/stewardship/local-environment/spec.md). Over HTTP the fake takes
only census publication's Member contact PUT, which it records here as one
more ``member`` edit (``fake_contact``, #1884).

``pk-stewardship fake-parishsoft-edit WORDS...`` (``execute``) is the
command-line half: it reads the current configuration on stdin, checks the
new edit against the generated parish, and writes the new configuration on
stdout, or prints a ``show`` report. It opens no connection and touches no
file.
"""

import json
import sys
from datetime import date, timedelta

from .synthetic_parish import (
    CHAIRPERSON_ROLE,
    MEMBER_ROLE,
    STAFF_ROLE,
    _day,
    contact_row,
)

# A bounded list: each edit is replayed at every fake start.
MAXIMUM_EDITS = 500
MAXIMUM_TEXT = 200
EDIT_KINDS = frozenset({"member", "family", "roster_add", "roster_remove", "pledge"})
ROLES = {role[1]: role for role in (MEMBER_ROLE, CHAIRPERSON_ROLE, STAFF_ROLE)}
# Short names for the fields a census check usually needs; the provider's own
# field name (as ``show`` prints it) is accepted too.
MEMBER_ALIASES = {
    "first": "firstName",
    "middle": "middleName",
    "last": "lastName",
    "nick": "nickName",
    "maiden": "maidenName",
    "suffix": "suffix",
    "salutation": "salutation",
    "prefix": "salutation",
    "gender": "sex",
    "language": "language",
    "email": "emailAddress",
    "mobile": "mobilePhone",
    "work": "workPhone",
    "birth": "birthdate",
    "death": "dateOfDeath",
    "status": "memberStatus",
    "type": "memberType",
    "marital": "maritalStatus",
    "family": "familyDUID",
}
FAMILY_ALIASES = {
    "email": "eMailAddress",
    "phone": "familyHomePhone",
    "mailing": "mailingName",
    "address1": "primaryAddress1",
    "address2": "primaryAddress2",
    "city": "primaryCity",
    "state": "primaryState",
    "postal": "primaryPostalCode",
    "envelope": "envelopeNumber",
    "group": "famGroupID",
    # The census email opt-out (review-pipeline spec, "What is confirmable").
    "no_mail": "sendNoMail",
}
# The fields an edit may set, with the JSON type the source loaders accept
# for each (``source/corpus.py``): an edit the command takes must never make
# the real refresh fail. Identities and derived counts are not editable; a
# Member moves by ``familyDUID``.
TEXT, DATE, WHOLE, COUNT, FLAG, GROUP, FAMILY = (
    "text",
    "date",
    "whole",
    "count",
    "flag",
    "group",
    "family",
)
MEMBER_FIELDS = {
    **dict.fromkeys(
        (
            "firstName",
            "middleName",
            "lastName",
            "nickName",
            "maidenName",
            "suffix",
            "salutation",
            "sex",
            "language",
            "emailAddress",
            "homePhone",
            "mobilePhone",
            "workPhone",
            "memberStatus",
            "memberType",
            "maritalStatus",
        ),
        TEXT,
    ),
    "birthdate": DATE,
    "dateOfDeath": DATE,
    "maritalStatusID": WHOLE,
    "family_PublishEMail": FLAG,
    "family_PublishPhone": FLAG,
    "familyDUID": FAMILY,
}
FAMILY_FIELDS = {
    **dict.fromkeys(
        (
            "firstName",
            "lastName",
            "mailingName",
            "eMailAddress",
            "familyHomePhone",
            "primaryPhone",
            "primaryAddress1",
            "primaryAddress2",
            "primaryAddress3",
            "primaryCity",
            "primaryState",
            "primaryPostalCode",
            "primaryZipPlus",
            "familyParticipationStatus",
        ),
        TEXT,
    ),
    "envelopeNumber": COUNT,
    "registeredOrganizationID": WHOLE,
    "famGroupID": GROUP,
    **dict.fromkeys(
        (
            "status",
            "sendNoMail",
            "hasSuspense",
            "primaryPublishEMail",
            "primaryPublishPhone",
            "primaryPublishAddress",
        ),
        FLAG,
    ),
}
# Fields that must always hold a value (null fails the loaders).
REQUIRED = frozenset({WHOLE, COUNT, FLAG, GROUP, FAMILY})


class EditError(ValueError):
    """An edit the parish cannot take; the message is plain and names no path."""


def _text(value):
    """A short printable string, or None for an empty value."""
    if value == "":
        return None
    if len(value) > MAXIMUM_TEXT or not value.isprintable():
        raise EditError(f"Values are at most {MAXIMUM_TEXT} printable characters.")
    return value


def _iso_date(value, what):
    """A civil date given as YYYY-MM-DD."""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise EditError(f"{what} must be a date as YYYY-MM-DD.") from None
    if parsed.isoformat() != value:
        raise EditError(f"{what} must be a date as YYYY-MM-DD.")
    return parsed


def served_date(value, what):
    """A YYYY-MM-DD civil date as the fake serves it, a midnight timestamp.

    EditError names ``what`` unless ``value`` is exactly YYYY-MM-DD. Public
    for ``fake_contact``, whose PUT dates are stored the same way.
    """
    return _day(_iso_date(value, what))


def _identifier(value, what):
    """A positive whole-number record id."""
    if type(value) is str and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 1 <= value < 2**31:
        raise EditError(f"{what} must be a positive whole number.")
    return value


def coerce(field, raw, kind):
    """One ``field=value`` word's value in the provider's own JSON type.

    ``kind`` is the field's entry in MEMBER_FIELDS or FAMILY_FIELDS. An empty
    value clears a text or date field (null). Dates are given as YYYY-MM-DD
    and served as ParishSoft's midnight timestamps; flags take true or false;
    ids take whole numbers (an envelope number may be 0).
    """
    if raw == "":
        if kind in REQUIRED:
            raise EditError(f"{field} cannot be empty.")
        return None
    if kind == DATE:
        return served_date(raw, field)
    if kind == FLAG:
        if raw not in ("true", "false"):
            raise EditError(f"{field} must be true or false.")
        return raw == "true"
    if kind == COUNT:
        if not raw.isdecimal() or int(raw) >= 2**31:
            raise EditError(f"{field} must be a whole number.")
        return int(raw)
    if kind in (WHOLE, GROUP, FAMILY):
        return _identifier(raw, field)
    return _text(raw)


def check_value(field, value, kind, parish):
    """A stored value is what ``coerce`` would produce and the parish can take.

    Run at every replay, so a hand-edited file the loaders would refuse stops
    the fake at start instead of failing a refresh. A Family group must be one
    of the parish's groups, and a moved Member's Family must exist and not be
    the held-back late-added Family.
    """
    if value is None:
        ok = kind not in REQUIRED
    elif kind == TEXT:
        ok = type(value) is str and 0 < len(value) <= MAXIMUM_TEXT
        ok = ok and value.isprintable()
    elif kind == DATE:
        ok = type(value) is str and value == _day(_iso_date(value[:10], field))
    elif kind == FLAG:
        ok = type(value) is bool
    else:
        low = 0 if kind == COUNT else 1
        ok = type(value) is int and low <= value < 2**31
    if not ok:
        raise EditError(f"The edit's value for {field} is not valid.")
    if kind == GROUP and value not in {
        row["famGroupID"] for row in parish.family_groups
    }:
        known = ", ".join(str(row["famGroupID"]) for row in parish.family_groups)
        raise EditError(f"{field} must be one of the parish's groups: {known}.")
    if kind == FAMILY:
        _find(parish.families, "familyDUID", value, "Family")
        _refuse_late(parish, family=value)


def _refuse_late(parish, *, family=None, member=None):
    """Refuse an edit that touches the held-back late-added Family.

    Until ``seed`` releases it the fake hides that Family, but not the rows
    an edit would hang on it (a pledge, a roster entry, a moved Member), and
    the loaders refuse a reference to a Family or Member they cannot see.
    """
    if member is not None:
        family = member["familyDUID"]
    if family == parish.late_family_id:
        raise EditError(
            f"Family {family} is the late-added Family the seed releases; "
            "edits cannot name it or its Members."
        )


def _fields(row, words, aliases, fields, what):
    """Parse ``field=value`` words against the editable fields of one row."""
    if not words:
        raise EditError(f"Give at least one field=value for the {what}.")
    result = {}
    for word in words:
        name, equals, raw = word.partition("=")
        if not equals:
            raise EditError(f"'{word}' is not field=value.")
        field = aliases.get(name, name)
        if field not in fields:
            known = ", ".join(sorted(aliases))
            raise EditError(
                f"The {what} has no editable field '{name}'. Short names: "
                f"{known}; or a field name as 'show' prints it."
            )
        result[field] = coerce(field, raw, fields[field])
    return result


def _options(words, allowed):
    """``name=value`` options after a command's positional words."""
    result = {}
    for word in words:
        name, equals, value = word.partition("=")
        if not equals or name not in allowed:
            raise EditError(f"Unknown option '{word}'; use {', '.join(allowed)}.")
        result[name] = value
    return result


def parse_edit(words, anchor):
    """One edit document from the command words (see ``execute``'s usage)."""
    if len(words) < 2:
        raise EditError(USAGE)
    kind = words[0]
    if kind in ("member", "family"):
        return {
            "kind": kind,
            "id": _identifier(words[1], f"The {kind} id"),
            "words": words[2:],
        }
    if kind == "roster" and len(words) >= 4 and words[1] in ("add", "remove"):
        ministry = _identifier(words[2], "The Ministry id")
        member = _identifier(words[3], "The Member id")
        if words[1] == "remove":
            if words[4:]:
                raise EditError("roster remove takes no options.")
            return {"kind": "roster_remove", "ministry": ministry, "member": member}
        options = _options(words[4:], ("role", "start"))
        role = options.get("role", MEMBER_ROLE[1])
        if role not in ROLES:
            raise EditError("role must be one of " + ", ".join(ROLES) + ".")
        start = options.get("start", anchor.isoformat())
        _iso_date(start, "start")
        return {
            "kind": "roster_add",
            "ministry": ministry,
            "member": member,
            "role": role,
            "start": start,
        }
    if kind == "pledge" and len(words) >= 3:
        family = _identifier(words[1], "The Family id")
        options = _options(words[3:], ("fund", "start"))
        amount = None if words[2] == "none" else _amount(words[2])
        start = options.get("start", date(anchor.year + 1, 1, 1).isoformat())
        _iso_date(start, "start")
        edit = {"kind": "pledge", "family": family, "start": start, "amount": amount}
        if "fund" in options:
            edit["fund"] = _identifier(options["fund"], "fund")
        return edit
    raise EditError(USAGE)


def _amount(raw):
    """A positive pledge amount, whole or with cents."""
    try:
        value = round(float(raw), 2)
    except ValueError:
        raise EditError("The pledge amount must be a number, or none.") from None
    if not 0 < value < 10**9:
        raise EditError("The pledge amount must be above zero, or none.")
    return int(value) if value == int(value) else value


def resolve(edit, parish):
    """Turn a parsed edit's ``words`` into checked provider fields, in place.

    Member and Family edits name fields by short or provider name; the
    stored edit keeps only provider names and typed values, so the fake never
    re-parses words at start.
    """
    if "words" not in edit:
        return edit
    rows = parish.members if edit["kind"] == "member" else parish.families
    key = "memberDUID" if edit["kind"] == "member" else "familyDUID"
    row = _find(rows, key, edit["id"], edit["kind"].capitalize())
    member = edit["kind"] == "member"
    fields = _fields(
        row,
        edit.pop("words"),
        MEMBER_ALIASES if member else FAMILY_ALIASES,
        MEMBER_FIELDS if member else FAMILY_FIELDS,
        "Member" if member else "Family",
    )
    edit["set"] = fields
    return edit


def _find(rows, key, identifier, what):
    """The one row whose ``key`` is ``identifier``; a missing one is refused."""
    for row in rows:
        if row[key] == identifier:
            return row
    raise EditError(f"There is no {what} {identifier} in the fake parish.")


def _check_shape(edit):
    """Structural check of one stored edit, before it touches the parish."""
    if type(edit) is not dict or edit.get("kind") not in EDIT_KINDS:
        raise EditError("A fake ParishSoft edit is malformed.")
    shapes = {
        "member": {"kind", "id", "set"},
        "family": {"kind", "id", "set"},
        "roster_add": {"kind", "ministry", "member", "role", "start"},
        "roster_remove": {"kind", "ministry", "member"},
        "pledge": {"kind", "family", "start", "amount"},
    }
    expected = shapes[edit["kind"]]
    extra = {"fund"} if edit["kind"] == "pledge" else set()
    if not expected <= set(edit) <= expected | extra:
        raise EditError("A fake ParishSoft edit is malformed.")
    if edit["kind"] in ("member", "family") and (
        type(edit["set"]) is not dict or not edit["set"]
    ):
        raise EditError("A fake ParishSoft edit is malformed.")


def apply_edit(parish, edit):
    """Apply one stored edit to the parish's collections, in place."""
    _check_shape(edit)
    if edit["kind"] in ("member", "family"):
        _apply_fields(parish, edit)
    elif edit["kind"] in ("roster_add", "roster_remove"):
        _apply_roster(parish, edit)
    else:
        _apply_pledge(parish, edit)


def _apply_fields(parish, edit):
    """Set a Member's or Family's fields; a Member's ``familyDUID`` moves it."""
    member = edit["kind"] == "member"
    row = _find(
        parish.members if member else parish.families,
        "memberDUID" if member else "familyDUID",
        edit["id"],
        "Member" if member else "Family",
    )
    _refuse_late(parish, **({"member": row} if member else {"family": edit["id"]}))
    fields = MEMBER_FIELDS if member else FAMILY_FIELDS
    for field, value in edit["set"].items():
        if field not in fields:
            raise EditError(f"The edit names a field that cannot be set: {field}.")
        check_value(field, value, fields[field], parish)
        row[field] = value


def _apply_roster(parish, edit):
    """Add one active roster entry, or remove every entry of the Member."""
    ministry = next((m for m in parish.ministries if m["id"] == edit["ministry"]), None)
    if ministry is None:
        raise EditError(f"There is no Ministry {edit['ministry']} in the fake parish.")
    member = _find(parish.members, "memberDUID", edit["member"], "Member")
    _refuse_late(parish, member=member)
    who, where = f"Member {member['memberDUID']}", f"Ministry {ministry['id']}"
    rows = parish.ministry_rosters[ministry["id"]]
    mine = [row for row in rows if row["memberId"] == member["memberDUID"]]
    if edit["kind"] == "roster_remove":
        if not mine:
            raise EditError(f"{who} is not on {where}.")
        rows[:] = [row for row in rows if row not in mine]
        return
    if any(row["endDate"] is None for row in mine):
        raise EditError(f"{who} is already on {where}.")
    role = ROLES.get(edit["role"]) if type(edit["role"]) is str else None
    if role is None:
        raise EditError("role must be one of " + ", ".join(ROLES) + ".")
    start = _day(_iso_date(edit["start"], "start"))
    # The loaders identify an entry by Member, Ministry, role, event and
    # start, so an ended entry with the same role and start would repeat it.
    if any(
        row["ministryRoleId"] == role[0] and row["startDate"] == start for row in mine
    ):
        raise EditError(
            f"{who} already has a {role[1]} entry on {where} starting "
            f"{edit['start']}; give another start=."
        )
    rows.append(
        {
            "memberId": member["memberDUID"],
            "familyId": member["familyDUID"],
            "firstName": member["firstName"],
            "lastName": member["lastName"],
            "ministryTypeId": ministry["id"],
            "ministryTypeName": ministry["name"],
            "ministryRoleId": role[0],
            "ministryRoleName": role[1],
            "ministryEventId": None,
            "ministryEventName": None,
            "trained": False,
            "subOnly": False,
            "startDate": start,
            "endDate": None,
        }
    )
    # The generator's order: by Member, stable within one Member.
    rows.sort(key=lambda row: row["memberId"])


def default_fund(parish):
    """The fund Create the campaign offers first: the first active, by name.

    The operator script passes the current campaign's own fund instead when
    it can read one; this is the fallback with no campaign.
    """
    return min(
        (row for row in parish.funds if row["active"]),
        key=lambda row: row["name"],
    )["fundId"]


def _apply_pledge(parish, edit):
    """Replace the Family's pledge for one fund and start date (or remove it)."""
    family = _find(parish.families, "familyDUID", edit["family"], "Family")
    _refuse_late(parish, family=family["familyDUID"])
    fund = edit.get("fund", default_fund(parish))
    if not any(row["fundId"] == fund for row in parish.funds):
        raise EditError(f"There is no fund {fund} in the fake parish.")
    start = _iso_date(edit["start"], "start")
    amount = edit["amount"]
    if amount is not None and (
        type(amount) not in (int, float) or isinstance(amount, bool) or not amount > 0
    ):
        raise EditError("The pledge amount must be above zero, or none.")
    same = [
        row
        for row in parish.pledges
        if row["familyID"] == family["familyDUID"]
        and row["fundID"] == fund
        and row["pledgeStartDate"] == _day(start)
    ]
    if amount is None:
        if not same:
            raise EditError(
                f"Family {family['familyDUID']} has no pledge to fund {fund} "
                f"starting {start}."
            )
        parish.pledges[:] = [row for row in parish.pledges if row not in same]
        return
    if same:
        # An existing pledge keeps its identity; only the amount changes.
        same[0]["currentPledgeAmount"] = amount
        parish.pledges[:] = [row for row in parish.pledges if row not in same[1:]]
        return
    parish.pledges.append(
        {
            "pledgeID": max((row["pledgeID"] for row in parish.pledges), default=0) + 1,
            "organizationID": parish.organization_id,
            "fundID": fund,
            "familyID": family["familyDUID"],
            "memberID": None,
            "pledgeDate": _day(start - timedelta(days=30)),
            "pledgeStartDate": _day(start),
            "currentPledgeAmount": amount,
            "pledgeFrequency": "Annual",
        }
    )


def rederive(parish):
    """Re-derive the copies the generator derives, after edits changed rows.

    The contact list from each Member and its Family, every Family's member
    count, and each Member-keyed roster row's Family and name, exactly as the
    generator first derives them, so an unedited parish is unchanged.
    """
    by_family = {row["familyDUID"]: row for row in parish.families}
    by_member = {row["memberDUID"]: row for row in parish.members}
    parish.contacts[:] = [
        contact_row(member, by_family[member["familyDUID"]])
        for member in parish.members
    ]
    counts = {}
    for member in parish.members:
        counts[member["familyDUID"]] = counts.get(member["familyDUID"], 0) + 1
    for family in parish.families:
        family["hasMembers"] = counts.get(family["familyDUID"], 0)
    for rows in parish.ministry_rosters.values():
        for row in rows:
            member = by_member[row["memberId"]]
            row.update(
                familyId=member["familyDUID"],
                firstName=member["firstName"],
                lastName=member["lastName"],
            )
    for rows in parish.family_workgroup_rosters.values():
        for row in rows:
            family = by_family[row["familyId"]]
            row.update(familyName=family["mailingName"], email=family["eMailAddress"])
    for rows in parish.member_workgroup_rosters.values():
        for row in rows:
            member = by_member[row["memberId"]]
            row.update(
                familyId=member["familyDUID"],
                firstName=member["firstName"],
                lastName=member["lastName"],
                emailAddress=member["emailAddress"],
            )


def apply_edits(parish, edits):
    """Replay every stored edit in order, then re-derive; no edits, no change."""
    if not edits:
        return parish
    if type(edits) not in (list, tuple) or len(edits) > MAXIMUM_EDITS:
        raise EditError("The fake ParishSoft edits list is malformed.")
    for edit in edits:
        apply_edit(parish, edit)
    rederive(parish)
    return parish


# -- The command line ---------------------------------------------------------

USAGE = """\
usage: fake-parishsoft-edit COMMAND
  member ID FIELD=VALUE...      set a Member's fields (family=ID moves the Member)
  family ID FIELD=VALUE...      set a Family's fields
  roster add MINISTRY MEMBER [role=Member|Chairperson|Staff] [start=YYYY-MM-DD]
  roster remove MINISTRY MEMBER remove the Member's entries on that Ministry
  pledge FAMILY AMOUNT|none [fund=ID] [start=YYYY-MM-DD]
                                set (or remove) a Family's pledge; default fund is
                                the first, default start 1 January after the anchor
  undo | clear                  drop the last edit, or every edit
  show                          the recorded edits
  show member ID | show family ID | show ministry ID
                                the record as the fake now serves it
An empty VALUE clears a field. Dates are YYYY-MM-DD."""

MUTATING = frozenset({"member", "family", "roster", "pledge", "undo", "clear"})


def _parish(document, edits):
    """The generated parish with ``edits`` applied, from a configuration document."""
    from .fake_parishsoft import FakeConfiguration
    from .synthetic_parish import generate

    configuration = FakeConfiguration.parse({**document, "edits": []})
    parish = generate(
        configuration.seed, configuration.families, configuration.anchor_date
    )
    return configuration, apply_edits(parish, edits)


def _show(words, document, parish):
    """A plain-text report: the edits, or one record as now served."""
    edits = document.get("edits", [])
    if not words:
        if not edits:
            return "No fake ParishSoft edits are recorded."
        return "\n".join(
            f"{number}. {json.dumps(edit, sort_keys=True)}"
            for number, edit in enumerate(edits, start=1)
        )
    if len(words) != 2 or words[0] not in ("member", "family", "ministry"):
        raise EditError(USAGE)
    identifier = _identifier(words[1], f"The {words[0]} id")
    if words[0] == "member":
        row = _find(parish.members, "memberDUID", identifier, "Member")
        rosters = [
            f"{rows[0]['ministryTypeId']} {rows[0]['ministryTypeName']}"
            for rows in (
                [r for r in roster if r["memberId"] == identifier]
                for roster in parish.ministry_rosters.values()
            )
            if rows
        ]
        return json.dumps({**row, "ministries": rosters}, indent=2)
    if words[0] == "family":
        row = _find(parish.families, "familyDUID", identifier, "Family")
        members = [
            f"{m['memberDUID']} {m['firstName']} {m['lastName']} ({m['memberType']})"
            for m in parish.members
            if m["familyDUID"] == identifier
        ]
        pledges = [p for p in parish.pledges if p["familyID"] == identifier]
        return json.dumps({**row, "members": members, "pledges": pledges}, indent=2)
    ministry = next((m for m in parish.ministries if m["id"] == identifier), None)
    if ministry is None:
        raise EditError(f"There is no Ministry {identifier} in the fake parish.")
    return json.dumps(
        {**ministry, "roster": parish.ministry_rosters[identifier]}, indent=2
    )


def edit_document(words, document):
    """The new configuration document for one mutating command.

    Every recorded edit plus the new one is replayed on a freshly generated
    parish, so a document this returns always starts the fake.
    """
    edits = list(document.get("edits", []))
    if words == ["undo"]:
        if not edits:
            raise EditError("There is no edit to undo.")
        edits.pop()
    elif words == ["clear"]:
        edits = []
    else:
        configuration, parish = _parish(document, edits)
        edit = resolve(parse_edit(words, configuration.anchor_date), parish)
        apply_edits(parish, [edit])
        edits.append(edit)
    _parish(document, edits)
    result = {**document, "edits": edits}
    if not edits:
        result.pop("edits")
    return result


def execute(words, stdin=None, stdout=None, stderr=None):
    """Console entry: configuration on stdin; new configuration or report on stdout.

    Exit 0 on success, 2 on a refused edit or unreadable input. Messages name
    records and fields, never file paths.
    """
    stdin, stdout, stderr = (
        stdin or sys.stdin,
        stdout or sys.stdout,
        stderr or sys.stderr,
    )
    if not words or words[0] in ("-h", "--help"):
        print(USAGE, file=stderr)
        return 0 if words else 2
    try:
        document = json.loads(stdin.read())
        if type(document) is not dict:
            raise ValueError
        if words[0] == "show":
            _, parish = _parish(document, document.get("edits", []))
            print(_show(words[1:], document, parish), file=stdout)
            return 0
        if words[0] not in MUTATING:
            raise EditError(USAGE)
        result = edit_document(words, document)
    except EditError as error:
        print(f"ERROR: {error}", file=stderr)
        return 2
    except ValueError:
        print(
            "ERROR: the fake ParishSoft configuration is unreadable or invalid",
            file=stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True), file=stdout)
    count = len(result.get("edits", []))
    print(f"{count} fake ParishSoft edit(s) recorded.", file=stderr)
    return 0
