"""The closed names of the counts a ParishSoft load is checked on.

Kept apart from ``loading`` (which imports the ParishSoft client) so the drop
count model, and the web process that reads it, need only these names.
"""

TREND_COLLECTIONS = ("family", "member", "ministry", "roster", "fund")
# Counts derived from the records rather than of them. ParishSoft can keep
# every row while dropping or nulling the fields eligibility is derived from
# (organization, status, member type, email); promotion would then make those
# Families ineligible at once (#320). The same loss threshold applies to these.
DERIVED_COUNTS = (
    "portal_eligible_families",
    "email_eligible_families",
    "active_head_families",
    "valid_email_contacts",
)
