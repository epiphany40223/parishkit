"""A campaign's Families rule: which ParishSoft Families it is sent to (#1393).

The rule is the campaign configuration value ``campaign_families``, chosen
on Create the campaign and never changed afterwards (Python's installer
preflight and SQL's stewardship_campaign_pointer_v1, migration 0175, both
refuse a change). Its shapes are in the data specification's "Campaign
Families": a preset (``{"rule": "active_parishioners"}`` or
``{"rule": "active_parishioners_and_staff"}``) or an Advanced rule. A
campaign without the value (every campaign created before the rule existed)
uses the legacy rule, which this module represents as ``None``; that value
is never written.

ParishSoft's matching itself is ``parishkit.parishsoft.family_matches_rule``,
which takes the same mapping.
"""

import hashlib
import json

from parishkit.parishsoft import (
    FAMILY_RULE_MEMBER_STATUSES,
    FAMILY_RULE_PRESET_GROUPS,
    NO_FAMILY_GROUP_ID,
)
from parishkit.stewardship.schema_primitives import invalid

KEY = "campaign_families"
# The load evidence key of the ParishSoft Family group lookup a refresh read.
GROUPS_KEY = "family_groups"
PRESETS = tuple(FAMILY_RULE_PRESET_GROUPS)
ADVANCED = "advanced"
DEFAULT = {"rule": "active_parishioners"}
# The one choice for Families with no group (famGroupID 0): stored with this
# name, which is not ParishSoft's own screen label, so it reads as a choice.
NO_GROUP = {"id": NO_FAMILY_GROUP_ID, "name": "(No Family Group)"}
GROUP_NAME_LIMIT = 200


def validate(value):
    """Refuse a ``campaign_families`` value that is not one canonical shape.

    Both Advanced lists are non-empty and in canonical order (groups by
    ascending id, statuses in ParishSoft's order Active, ActOther, Unknown),
    so one rule has one stored form; SQL's configuration guard (0175) checks
    the same shapes.
    """
    if type(value) is not dict or type(value.get("rule")) is not str:
        invalid()
    if value["rule"] in PRESETS:
        if set(value) != {"rule"}:
            invalid()
        return
    if value["rule"] != ADVANCED or set(value) != {
        "rule",
        "registered",
        "family_groups",
        "member_statuses",
    }:
        invalid()
    if type(value["registered"]) is not bool:
        invalid()
    groups, statuses = value["family_groups"], value["member_statuses"]
    if type(groups) is not list or not groups or type(statuses) is not list:
        invalid()
    for group in groups:
        if (
            type(group) is not dict
            or set(group) != {"id", "name"}
            or type(group["id"]) is not int
            or not 0 <= group["id"] < 2**31
            or type(group["name"]) is not str
            or not 1 <= len(group["name"]) <= GROUP_NAME_LIMIT
            or (group["id"] == NO_FAMILY_GROUP_ID and group != NO_GROUP)
        ):
            invalid()
    ids = [group["id"] for group in groups]
    if ids != sorted(set(ids)):
        invalid()
    if not statuses or statuses != [
        status for status in FAMILY_RULE_MEMBER_STATUSES if status in statuses
    ]:
        invalid()


def stored_rule(values):
    """The campaign's rule from its configuration values; ``None`` is legacy."""
    return values.get(KEY) if values is not None else None


def digest(rule):
    """A stable digest of one rule (legacy: ``None``) for load evidence.

    The drop check compares eligibility counts only across snapshots whose
    loads recorded the same digest.
    """
    if rule is None:
        return None
    document = json.dumps(rule, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode()).hexdigest()


def missing_groups(rule, family_groups):
    """The rule's chosen groups that ``family_groups`` (id -> name) lacks.

    Presets name groups; Advanced chooses them by id (the no-group choice is
    never missing). Each missing group is ``{"id", "name"}``, id ``None`` for
    a preset's name, in the rule's order.
    """
    if rule is None:
        return []
    if rule["rule"] in PRESETS:
        names = set(family_groups.values())
        return [
            {"id": None, "name": name}
            for name in FAMILY_RULE_PRESET_GROUPS[rule["rule"]]
            if name not in names
        ]
    return [
        group
        for group in rule["family_groups"]
        if group["id"] != NO_FAMILY_GROUP_ID and group["id"] not in family_groups
    ]


def evidence(rule, family_groups):
    """The load evidence a refresh records about the rule it applied.

    ``family_groups`` is the ParishSoft group lookup (id -> name). The
    legacy rule records nothing here, so a legacy refresh's evidence keeps
    exactly today's keys apart from the group lookup.
    """
    if rule is None:
        return None
    return {
        "rule": rule,
        "digest": digest(rule),
        "missing_groups": missing_groups(rule, family_groups),
    }


def recorded_digest(cursor):
    """The rule digest a snapshot cursor's load recorded (legacy: ``None``)."""
    load = cursor.get("load") if type(cursor) is dict else None
    value = load.get(KEY) if type(load) is dict else None
    return value.get("digest") if type(value) is dict else None


def recorded_rule(cursor):
    """The rule a snapshot cursor's load recorded (legacy: ``None``)."""
    load = cursor.get("load") if type(cursor) is dict else None
    value = load.get(KEY) if type(load) is dict else None
    return value.get("rule") if type(value) is dict else None


def group_lookup_evidence(family_groups):
    """The group lookup as load evidence: every ``{id, name}`` by id."""
    return [
        {"id": identifier, "name": name}
        for identifier, name in sorted(family_groups.items())
    ]
