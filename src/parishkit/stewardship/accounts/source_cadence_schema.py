"""Versioned nightly source cadence; retained configuration schemas stay frozen."""

import re

from parishkit.stewardship.schema_primitives import invalid

SCHEMA = "source-cadence-v8"
REQUEST_SCHEMA = "source-cadence-patch-v8"
RECOVERY_SCHEMA = "operator-recovery-cadence-v8"
CREDENTIAL_SCHEMA = "integration-credential-cadence-v8"
DEFAULT_TIME = "02:00"


def uses_cadence(document):
    """Detect the explicit new setting only in a validated configuration envelope."""
    return any(
        "nightly_time" in row["values"]["settings"]
        for row in document["sections"].get("integrations", [])
        if row["values"].get("kind") == "parishsoft"
        and isinstance(row["values"].get("settings"), dict)
    )


def validate_sections(document):
    """Validate one new local-time field, then all retained content/policy rules."""
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
                row = row | {
                    "values": values
                    | {
                        "settings": {
                            name: value
                            for name, value in settings.items()
                            if name != "nightly_time"
                        }
                    }
                }
        rows.append(row)
    _validate_v5_sections(
        document | {"sections": document["sections"] | {"integrations": rows}}
    )
