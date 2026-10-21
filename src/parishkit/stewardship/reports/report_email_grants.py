"""Report email table grants (#1786), by login, as the digests' are.

- The general worker prepares sends: it updates its preparations and writes
  the snapshot and recipient rows (the snapshot holds the send's only copy
  of Family data).
- The scheduler allocates preparations and sees only opaque bindings.
- Web records Send now requests and lists sends by their bindings, never
  their content.
- Mail dispatch reads a recipient's snapshot, whose kept email is what it
  sends; the scheduler's dispatch reads see only bindings.

Every write is admitted by the tables' own guards (frozen file 0156).
Nothing exercises these until the conversion admits report schedules.
"""

PREPARATION = "stewardship_report_email_preparation"
SNAPSHOT = "stewardship_report_email_snapshot"
RECIPIENT = "stewardship_report_email_recipient"
MANUAL = "stewardship_report_email_manual_request"
COMPLETION = "stewardship_report_email_completion_ready"
TABLES = (MANUAL, PREPARATION, SNAPSHOT, RECIPIENT, COMPLETION)
# The columns that bind a snapshot or recipient without its content.
SNAPSHOT_BINDING = {"id", "preparation_id", "campaign_id", "has_content"}
RECIPIENT_BINDING = {"id", "snapshot_id", "outbox_id", "disposition"}


def _columns(columns, table, names):
    """Add SELECT column grants without dropping earlier ones."""
    columns.setdefault(table, {}).setdefault("SELECT", set()).update(names)


def add_report_email_grants(tables, columns, *, worker):
    """The worker prepares and captures sends; the scheduler allocates them."""
    for table in (MANUAL, COMPLETION):
        tables.setdefault(table, set()).add("SELECT")
    tables.setdefault(PREPARATION, set()).update(
        {"SELECT", "UPDATE" if worker else "INSERT"}
    )
    if not worker:
        _columns(columns, SNAPSHOT, SNAPSHOT_BINDING)
        _columns(columns, RECIPIENT, RECIPIENT_BINDING)
        return
    for table in (SNAPSHOT, RECIPIENT):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    # The open-requests read (and the snapshot guard, which runs it with the
    # worker's rights) tells open from followed-up requests.
    _columns(columns, "stewardship_additional_information", {"followed_up_at"})


def add_report_email_dispatch_grants(tables, columns, *, private):
    """Mail dispatch reads the kept email; scheduler recovery sees bindings."""
    for table in (PREPARATION, COMPLETION):
        tables.setdefault(table, set()).add("SELECT")
    if private:
        for table in (SNAPSHOT, RECIPIENT):
            tables.setdefault(table, set()).add("SELECT")
        return
    _columns(columns, SNAPSHOT, SNAPSHOT_BINDING)
    _columns(columns, RECIPIENT, RECIPIENT_BINDING)


def add_report_email_web_grants(tables, columns):
    """Record Send now; read sends by their bindings, never their content."""
    tables.setdefault(MANUAL, set()).update({"SELECT", "INSERT"})
    add_report_email_dispatch_grants(tables, columns, private=False)
