"""Web login grants for imported past-campaign archives (#1018, slice 2).

The Admin web process (and ``pk-admin history``, which runs as it) reviews,
applies and removes archives. It inserts a preview and its rows, deletes
expired previews and the rows of a removed import, and changes only an
archive's state and its who and when; the tables' guards admit only those
changes (``schema/migrations/0059_campaign_archives.sql``). No other login
reads them.
"""

ARCHIVE_TABLE = "stewardship_campaign_archive"
CHILD_TABLES = (
    "stewardship_campaign_archive_day",
    "stewardship_campaign_archive_band",
    "stewardship_campaign_archive_family",
)
# The archive columns an Apply or a removal sets.
STATE_COLUMNS = frozenset(
    {"state", "version", "imported_by_id", "imported_at", "removed_by_id", "removed_at"}
)


def add_archive_web_grants(tables, columns):
    """Read, add and delete archives and their rows; change only the state."""
    for table in (ARCHIVE_TABLE, *CHILD_TABLES):
        tables[table] = {"SELECT", "INSERT", "DELETE"}
    columns[ARCHIVE_TABLE] = {"UPDATE": set(STATE_COLUMNS)}
