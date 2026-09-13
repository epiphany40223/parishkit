"""Closed non-secret settings for target-owned integration validation.

An API key is not sufficient context for checking the correct ParishSoft tenant;
Workspace delegation and Slack channel scope likewise must be explicit. These
values are immutable request evidence, never arbitrary provider call arguments.
"""

import re

from parishkit.config import ConfigError

from .policy_schema import normalized_email

FIELDS = {
    "parishsoft": frozenset({"organization_id"}),
    "google_workspace": frozenset(
        {"delegated_email", "sender", "reply_to", "recipient"}
    ),
    "slack": frozenset({"channel_id"}),
}


def validated_context(target, values):
    """Reject unknown keys, coercions and noncanonical settings before staging."""
    if (
        type(target) is not str
        or target not in FIELDS
        or type(values) is not dict
        or set(values) != FIELDS[target]
    ):
        raise ConfigError("Invalid provider validation context.")
    if target == "parishsoft":
        value = values["organization_id"]
        valid = type(value) is int and 1 <= value < 2**31
    elif target == "slack":
        value = values["channel_id"]
        valid = (
            type(value) is str
            and re.fullmatch(r"[CG][A-Z0-9]{1,63}", value) is not None
        )
    else:
        try:
            valid = all(normalized_email(value) == value for value in values.values())
        except (ConfigError, ValueError, TypeError):
            valid = False
    if not valid:
        raise ConfigError("Invalid provider validation context.")
    return dict(values)
