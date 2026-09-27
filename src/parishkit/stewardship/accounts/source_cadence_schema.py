"""Versioned nightly source cadence; retained configuration schemas stay frozen."""

import re

from parishkit.stewardship.schema_primitives import invalid

SCHEMA = "source-cadence-v8"
REQUEST_SCHEMA = "source-cadence-patch-v8"
RECOVERY_SCHEMA = "operator-recovery-cadence-v8"
CREDENTIAL_SCHEMA = "integration-credential-cadence-v8"
DEFAULT_TIME = "02:00"


# Settings this schema adds to the ParishSoft integration, over v5.
CADENCE_SETTINGS = ("nightly_time", "full_refresh")


def uses_cadence(document):
    """Detect the explicit new settings only in a validated configuration envelope."""
    return any(
        name in row["values"]["settings"]
        for name in CADENCE_SETTINGS
        for row in document["sections"].get("integrations", [])
        if row["values"].get("kind") == "parishsoft"
        and isinstance(row["values"].get("settings"), dict)
    )


def validate_sections(document):
    """Validate the refresh time and frequency, then all retained v5 rules."""
    from .configuration_schema import _validate_v5_sections

    rows = []
    for row in document["sections"].get("integrations", []):
        values = row["values"]
        if values.get("kind") == "parishsoft":
            settings = values.get("settings")
            if not isinstance(settings, dict):
                invalid()
            if "nightly_time" in settings:
                time = settings["nightly_time"]
                if (
                    type(time) is not str
                    or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time) is None
                ):
                    invalid()
            if "full_refresh" in settings and settings["full_refresh"] not in (
                "daily",
                "hourly",
                "quarter_hour",
            ):
                invalid()
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
