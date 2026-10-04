"""Versioned nightly source cadence; retained configuration schemas stay frozen."""

from parishkit.stewardship.schema_primitives import invalid
from parishkit.stewardship.source.cadence import (
    DEFAULT_TIME,
    DELTA_REFRESHES,
    FREQUENCIES,
    canonical_time,
    canonical_times,
)

SCHEMA = "source-cadence-v8"
REQUEST_SCHEMA = "source-cadence-patch-v8"
RECOVERY_SCHEMA = "operator-recovery-cadence-v8"
CREDENTIAL_SCHEMA = "integration-credential-cadence-v8"


# Settings this schema adds to the ParishSoft integration, over v5. Every one
# is optional, so documents written before a setting existed stay valid:
# ``full_refresh_times`` (1–8 sorted unique local HH:MM times, including the
# nightly time) and ``delta_refresh`` arrived with #465.
CADENCE_SETTINGS = (
    "nightly_time",
    "full_refresh",
    "full_refresh_times",
    "delta_refresh",
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
