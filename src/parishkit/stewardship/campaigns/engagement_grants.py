"""Explicit SQL access for durable Family engagement (#477)."""

# The columns the monotonic upsert's ON CONFLICT DO UPDATE sets. Identity
# (family, mode, epoch) and creation stay unwritable; the guard also refuses
# any value that would move a first_* instant later or the furthest step back.
ENGAGEMENT_UPDATE_COLUMNS = frozenset(
    {
        "first_link_at",
        "first_form_at",
        "first_progress_at",
        "furthest_section",
        "furthest_at",
        "last_seen_at",
        "version",
        "actor_id",
        "correlation_id",
    }
)


def add_engagement_web_grants(tables, columns):
    """Web inserts and advances engagement rows; it never deletes one."""
    tables["stewardship_family_engagement"] = {"SELECT", "INSERT"}
    columns["stewardship_family_engagement"] = {
        "UPDATE": set(ENGAGEMENT_UPDATE_COLUMNS)
    }
