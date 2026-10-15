"""The retired local Ministry activity section of the configuration (#1267).

Until #1267 an Administrator could mark a ParishSoft Ministry inactive for
the whole parish, in the configuration's ``ministries`` section
(``{organization_id, ministry_duid, active}`` records). That override is
gone: every Ministry ParishSoft lists can be chosen for a campaign, and the
campaign's Ministry selections alone decide what Families see.

What stays is what applied history needs. The schema names below are
frozen discriminators of stored configurations and request formats, so they
keep their meaning. ``validate_records`` still checks a stored section's
shape, but only applied, digest-verified history (``trusted_content``) may
hold records: a new or changed configuration with any ``ministries`` record
is refused with an error that names the section, as a retired content slot
is (``content_schema``).
"""

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import invalid

from .content_trust import trusts_all

SCHEMA = "ministry-activity-v4"
REQUEST_SCHEMA = "ministry-activity-patch-v4"
RECOVERY_SCHEMA = "operator-recovery-ministry-v4"

RETIRED_MESSAGE = (
    "The configuration's ministries section (local Ministry activity) was "
    "removed (#1267); delete its records and try again."
)


def validate_records(records):
    """Refuse new records; check stored history's exact identities and shape.

    Applied history is validated under ``trusted_content()``, which trusts
    every record; only there may the retired section still hold records.
    """
    if records and not trusts_all():
        raise ConfigError(RETIRED_MESSAGE)
    seen = set()
    for record in records:
        values = record["values"]
        if set(values) != {"organization_id", "ministry_duid", "active"}:
            invalid()
        identity = (values["organization_id"], values["ministry_duid"])
        if (
            any(type(value) is not int or not 1 <= value < 2**31 for value in identity)
            or type(values["active"]) is not bool
            or identity in seen
        ):
            invalid()
        seen.add(identity)
