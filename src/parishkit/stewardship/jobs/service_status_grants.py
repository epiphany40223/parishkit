"""SQL authority over the service status records, per login (ADM-13, #530).

Every online service's login (web, worker, scheduler, mail dispatch, the
configuration installer and each credential installer) may insert and
refresh status rows; the guard in
``schema/migrations/0007_system_health_records.sql`` then limits each login to
its own service's rows and sets every time from the database clock. The
installers had no such write grant before ADM-13. A writer other than web
reads only ``id`` (its refresh names its own row by id); web reads the whole
table for the System health page. The worker's hourly housekeeping also
deletes rows not reported for a day, which needs ``reported_at``.
"""

TABLE = "stewardship_service_status"
# The columns a refresh sets. The guard sets reported_at and sender_since
# itself from the database clock, so neither is granted.
REPORT_COLUMNS = frozenset({"debug_logging", "sender_state", "sender_until"})


def add_service_status_writer_grants(tables, columns, *, reads_table=False):
    """Insert and refresh this login's own rows (the guard enforces "own").

    ``reads_table`` is true for web, which already reads the whole table, so
    it needs no column read of ``id``.
    """
    tables.setdefault(TABLE, set()).add("INSERT")
    privileges = columns.setdefault(TABLE, {})
    privileges.setdefault("UPDATE", set()).update(REPORT_COLUMNS)
    if not reads_table:
        privileges.setdefault("SELECT", set()).add("id")


def add_service_status_housekeeping_grants(tables, columns):
    """Worker: delete rows whose process has not reported for a day."""
    add_service_status_writer_grants(tables, columns)
    tables[TABLE].add("DELETE")
    columns[TABLE]["SELECT"].add("reported_at")
