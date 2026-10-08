"""Versioned nightly source cadence; retained configuration schemas stay frozen."""

from parishkit.stewardship.schema_primitives import invalid
from parishkit.stewardship.source.cadence import (
    DEFAULT_TIME,
    DELTA_REFRESHES,
    FREQUENCIES,
    MAX_DAILY_TIMES,
    MAX_LEGACY_FULL_TIMES,
    canonical_list,
    canonical_time,
    canonical_times,
)
from parishkit.stewardship.source.refresh_rules import spacing_conflicts, valid_shape

SCHEMA = "source-cadence-v8"
REQUEST_SCHEMA = "source-cadence-patch-v8"
RECOVERY_SCHEMA = "operator-recovery-cadence-v8"
CREDENTIAL_SCHEMA = "integration-credential-cadence-v8"


# Settings this schema adds to the ParishSoft integration, over v5. Every one
# is optional, so documents written before a setting existed stay valid:
# ``full_refresh_times`` (sorted unique local HH:MM times, including the
# nightly time) and ``delta_refresh`` arrived with #465;
# ``quick_refresh_times`` and ``refresh_rules`` with the integrated refresh
# schedule (#632), which also lifted the cap of eight full times.
CADENCE_SETTINGS = (
    "nightly_time",
    "full_refresh",
    "full_refresh_times",
    "delta_refresh",
    "quick_refresh_times",
    "refresh_rules",
)


def uses_cadence(document):
    """Detect the explicit new settings only in a validated configuration envelope."""
    return any(
        name in row["values"]["settings"]
        for name in CADENCE_SETTINGS
        for row in document["sections"].get("integrations", [])
        if row["values"].get("kind") == "parishsoft"
        and isinstance(row["values"].get("settings"), dict)
    )


def _validate_cadence(settings):
    """Refuse a malformed refresh time, time list, frequency or delta cadence."""
    if "nightly_time" in settings and not canonical_time(settings["nightly_time"]):
        invalid()
    if "full_refresh" in settings and settings["full_refresh"] not in FREQUENCIES:
        invalid()
    if "full_refresh_times" in settings and not canonical_times(
        settings.get("nightly_time", DEFAULT_TIME), settings["full_refresh_times"]
    ):
        invalid()
    if "delta_refresh" in settings and settings["delta_refresh"] not in DELTA_REFRESHES:
        invalid()
    if "refresh_rules" in settings:
        _validate_rules_schedule(settings)
    elif (
        "quick_refresh_times" in settings
        or settings.get("delta_refresh") == "times"
        or len(settings.get("full_refresh_times", ())) > MAX_LEGACY_FULL_TIMES
    ):
        # Listed quick times and more than eight full times exist only in a
        # schedule saved with its rules: a document without ``refresh_rules``
        # is an existing schedule, held to the old limits, and its slots are
        # created exactly as before (#632).
        invalid()


def _validate_rules_schedule(settings):
    """Check the shape of a schedule saved with its rules (#632).

    Only the shape: the rules' closed fields; daily full times with listed
    quick times (``delta_refresh`` "times") or none ("off"); no time in both
    lists; at most ``MAX_DAILY_TIMES`` in all; and every two times at least
    15 minutes apart around the clock. Whether the lists are what the rules
    produce, and the quarter-hour rule for new times, are the settings form's
    and the command line's shared check (``refresh_rules.check_schedule``);
    a stored document is never re-derived.
    """
    rules = settings["refresh_rules"]
    full = settings.get("full_refresh_times")
    quick = settings.get("quick_refresh_times", [])
    delta = settings.get("delta_refresh")
    if (
        not valid_shape(rules)
        or settings.get("full_refresh", "daily") != "daily"
        or full is None
        or "nightly_time" not in settings
        or delta not in ("times", "off")
        or (delta == "times") != ("quick_refresh_times" in settings)
        or ("quick_refresh_times" in settings and not canonical_list(quick))
        or set(full) & set(quick)
        or len(full) + len(quick) > MAX_DAILY_TIMES
        or spacing_conflicts([*full, *quick])
    ):
        invalid()


def validate_sections(document):
    """Validate the refresh schedule settings, then all retained v5 rules."""
    from .configuration_schema import _validate_v5_sections

    rows = []
    for row in document["sections"].get("integrations", []):
        values = row["values"]
        if values.get("kind") == "parishsoft":
            settings = values.get("settings")
            if not isinstance(settings, dict):
                invalid()
            _validate_cadence(settings)
            row = row | {
                "values": values
                | {
                    "settings": {
                        name: value
                        for name, value in settings.items()
                        if name not in CADENCE_SETTINGS
                    }
                }
            }
        rows.append(row)
    _validate_v5_sections(
        document | {"sections": document["sections"] | {"integrations": rows}}
    )
