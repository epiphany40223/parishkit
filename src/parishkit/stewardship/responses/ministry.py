"""Pure Ministry visibility, roster projection and complete-answer validation."""

import unicodedata
from dataclasses import dataclass

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.ministry_activity import active_ministries

MAX_MINISTRY_LABEL = 512


class InvalidMinistrySource(ValueError):
    """Unrepresentable scoped catalog/roster data, never its private payload."""


class InvalidMinistryAnswers(ValueError):
    """Static errors use only trusted entity paths, not forged input identifiers."""

    def __init__(self, fields):
        self.fields = dict(fields)
        super().__init__("Please review the Ministry choices.")


def _unavailable():
    """Keep source values and malformed identifiers out of error diagnostics."""
    raise InvalidMinistrySource("The Ministry form inputs are unavailable.") from None


def _duid(value):
    """Use the source system's exact positive integer identity domain."""
    return type(value) is int and 0 < value < 2**31


def _source_key(value):
    """Reject alternate spellings instead of aliasing another source key."""
    if type(value) is not str or not value.isascii() or not value.isdecimal():
        _unavailable()
    if len(value) > 10 or not _duid(int(value)) or str(int(value)) != value:
        _unavailable()
    return int(value)


@dataclass(frozen=True)
class MinistryOption:
    """One visible catalog identity and its current bounded display label."""

    duid: int
    name: str


@dataclass(frozen=True)
class MinistryInputs:
    """Only visible options and this Family's current active-Member memberships."""

    options: tuple[MinistryOption, ...]
    memberships: tuple[tuple[int, tuple[int, ...]], ...]

    def comparison(self):
        """Option labels, identities and scoped roster all affect reviewed inputs."""
        return (
            tuple((option.duid, option.name) for option in self.options),
            self.memberships,
        )


def ministry_inputs(
    catalog, roster, *, member_duids, selected_duids, document, organization_id
):
    """Intersect current catalog, parish activity and campaign selection.

    The caller supplies a single retained snapshot and its corresponding applied
    policy document. Upstream's incidental active flag is not the local activity
    policy. Hidden labels and memberships never enter the returned projection.
    Multiple current roster roles reduce to one Member/Ministry membership.
    """
    if (
        type(catalog) not in {list, tuple}
        or type(roster) not in {list, tuple}
        or type(member_duids) not in {list, tuple}
        or type(selected_duids) not in {list, tuple}
        or any(not _duid(value) for value in member_duids)
        or len(member_duids) != len(set(member_duids))
        or any(not _duid(value) for value in selected_duids)
        or len(selected_duids) != len(set(selected_duids))
    ):
        _unavailable()
    indexed = {}
    for row in catalog:
        if (
            type(row) is not dict
            or not _duid(row.get("id"))
            or row.get("catalog_present") is not True
            or row["id"] in indexed
        ):
            _unavailable()
        indexed[row["id"]] = row
    try:
        visible = active_ministries(
            document,
            organization_id=organization_id,
            catalog_duids=frozenset(indexed),
        ) & frozenset(selected_duids)
    except (ConfigError, KeyError, TypeError, ValueError):
        _unavailable()
    options = []
    for identifier in visible:
        name = indexed[identifier].get("name")
        if type(name) is not str:
            _unavailable()
        name = unicodedata.normalize("NFC", name.strip())
        if (
            not name
            or len(name) > MAX_MINISTRY_LABEL
            or any(unicodedata.category(char).startswith("C") for char in name)
        ):
            _unavailable()
        options.append(MinistryOption(identifier, name))
    options.sort(key=lambda option: (option.name.casefold(), option.duid))
    memberships = {identifier: set() for identifier in member_duids}
    for row in roster:
        if type(row) is not dict or type(row.get("current")) is not bool:
            _unavailable()
        member = _source_key(row.get("member_key"))
        ministry = _source_key(row.get("ministry_key"))
        if member not in memberships:
            _unavailable()
        if row["current"] and ministry in visible:
            memberships[member].add(ministry)
    return MinistryInputs(
        tuple(options),
        tuple(
            (member, tuple(sorted(values)))
            for member, values in sorted(memberships.items())
        ),
    )


def _choices(value, allowed):
    """A set of exact offered DUIDs is serialized in deterministic numeric order."""
    if (
        type(value) is not list
        or len(value) > len(allowed)
        or any(not _duid(item) or item not in allowed for item in value)
        or len(value) != len(set(value))
    ):
        return None
    return sorted(value)


def validate_ministry_answers(payload, inputs, *, terminal_members, proposed_members):
    """Validate complete visible choices, never interpret hidden omissions here.

    Terminal/proposed identity sets come from the already validated census
    aggregate. The later derivation owner distinguishes an informed withdrawal
    from a request absent because its Ministry is hidden or its Member terminal.
    Disabled modules accept an empty object only and cannot create work.
    """
    if inputs is None:
        if type(payload) is not dict or payload:
            raise InvalidMinistryAnswers({"ministries": "This section is not enabled."})
        return {}
    if type(payload) is not dict or set(payload) != {"members", "proposed_members"}:
        raise InvalidMinistryAnswers({"ministries": "Review the Ministry section."})
    existing = {
        str(member): frozenset(values)
        for member, values in inputs.memberships
        if str(member) not in terminal_members
    }
    groups = {"members": set(existing), "proposed_members": set(proposed_members)}
    errors, result = {}, {"members": {}, "proposed_members": {}}
    offered = frozenset(option.duid for option in inputs.options)
    for group, identifiers in groups.items():
        entries = payload[group]
        if type(entries) is not dict or set(entries) != identifiers:
            errors[f"ministries.{group}"] = "Review the current household choices."
            continue
        for identifier in sorted(identifiers):
            entry, path = entries[identifier], f"ministries.{group}.{identifier}"
            names = {"join", "leave"} if group == "members" else {"join"}
            if type(entry) is not dict or set(entry) != names:
                errors[path] = "Review this person's Ministry choices."
                continue
            current = existing[identifier] if group == "members" else frozenset()
            normalized = {}
            for name in sorted(names):
                allowed = offered - current if name == "join" else current
                choices = _choices(entry[name], allowed)
                if choices is None:
                    errors[f"{path}.{name}"] = (
                        "Choose only currently offered Ministries."
                    )
                else:
                    normalized[name] = choices
            result[group][identifier] = normalized
    if errors:
        raise InvalidMinistryAnswers(errors)
    return result
