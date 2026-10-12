"""Web login grants for imported past-campaign archives (#1018, slice 2).

The Admin web process (and ``pk-admin history``, which runs as it) reviews,
applies and removes archives. It inserts a preview and its rows, deletes
expired previews and the rows of a removed import, and changes only an
archive's state and its who and when; the tables' guards admit only those
changes (``schema/migrations/0059_campaign_archives.sql``). The general
worker also reads the archive, day and band tables, for the daily digest's
Compared with past campaigns section (#1018 slice 7;
``digest_grants.add_digest_grants``), never the Family rows; no other login
reads them.

Stored Family rows are read only inside the database (campaign-history
spec, "Privacy"): the web login may read a Family row's ``id`` and
``archive_id`` (to count and delete an archive's rows) and its
``created_at`` (which the ORM's insert returns, as the column has a database
default), never its id system, id or first campaign day.
"""

ARCHIVE_TABLE = "stewardship_campaign_archive"
CHILD_TABLES = (
    "stewardship_campaign_archive_day",
    "stewardship_campaign_archive_band",
    "stewardship_campaign_archive_family",
)
FAMILY_TABLE = "stewardship_campaign_archive_family"
# The Family-row columns the web login reads; see the module docstring.
FAMILY_READ_COLUMNS = frozenset({"id", "archive_id", "created_at"})
# The archive columns an Apply or a removal sets.
STATE_COLUMNS = frozenset(
    {"state", "version", "imported_by_id", "imported_at", "removed_by_id", "removed_at"}
)


def add_archive_web_grants(tables, columns):
    """Read, add and delete archives and their rows; change only the state.

    Family rows are added and deleted, but read only by FAMILY_READ_COLUMNS.
    """
    for table in (ARCHIVE_TABLE, *CHILD_TABLES):
        tables[table] = {"SELECT", "INSERT", "DELETE"}
    tables[FAMILY_TABLE] = {"INSERT", "DELETE"}
    columns[ARCHIVE_TABLE] = {"UPDATE": set(STATE_COLUMNS)}
    columns[FAMILY_TABLE] = {"SELECT": set(FAMILY_READ_COLUMNS)}
