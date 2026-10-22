"""Report email table grants (#1786), by login, as the digests' are.

- The general worker prepares sends: it updates its preparations and writes
  the snapshot and recipient rows (the snapshot holds the send's only copy
  of Family data).
- The scheduler allocates preparations and sees only opaque bindings.
- Web records Send now requests and lists sends by their bindings, never
  their content.
- Mail dispatch reads a recipient's snapshot, whose kept email is what it
  sends; the scheduler's dispatch reads see only bindings.

Every write is admitted by the tables' own guards (frozen file 0156);
the conversion (0161, #1786) admits report schedules.
"""

PREPARATION = "stewardship_report_email_preparation"
SNAPSHOT = "stewardship_report_email_snapshot"
RECIPIENT = "stewardship_report_email_recipient"
MANUAL = "stewardship_report_email_manual_request"
COMPLETION = "stewardship_report_email_completion_ready"
# An Administrator's coverage decision about an earlier report email (0164,
# #1872), which web records, with its evidence note, and reads, and the
# worker's coverage comparison reads.
DECISION = "stewardship_report_email_coverage_decision"
TABLES = (MANUAL, PREPARATION, SNAPSHOT, RECIPIENT, COMPLETION, DECISION)
# The columns that bind a snapshot or recipient without its content.
# When its email was made is a binding too: Report emails lists the sends.
SNAPSHOT_BINDING = {"id", "preparation_id", "campaign_id", "has_content", "created_at"}
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
        # Scheduler selection of fact generations calls the invoker-rights
        # stewardship_fact_disposable (0161), which asks whether a report
        # email snapshot still holds a generation: the generation's id only,
        # never the email.
        _columns(columns, SNAPSHOT, {"fact_set_id"})
        _columns(columns, RECIPIENT, RECIPIENT_BINDING)
        return
    for table in (SNAPSHOT, RECIPIENT):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    # The coverage comparison (stewardship_report_email_prior_v1), which the
    # fanout and the recipient guard run with the worker's rights, reads
    # the Administrator's coverage decisions (#1872).
    tables.setdefault(DECISION, set()).add("SELECT")
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


# What a saved copy shows (report_email_copy_views, #1946): the email as
# sent and its images, each block's kept data, the report's values (its
# name and as-of choice), the frozen recipients by kind, and the pinned
# configuration (date format, names) and fact generation (its charts).
COPY_COLUMNS = {
    "subject",
    "html",
    "images",
    "blocks",
    "values",
    "recipients",
    "configuration_id",
    "fact_set_id",
}


def add_report_email_web_grants(tables, columns):
    """Record Send now and coverage decisions; read sends and saved copies."""
    tables.setdefault(MANUAL, set()).update({"SELECT", "INSERT"})
    tables.setdefault(DECISION, set()).update({"SELECT", "INSERT"})
    add_report_email_dispatch_grants(tables, columns, private=False)
    _columns(columns, SNAPSHOT, COPY_COLUMNS)
    # Sent mail's retry admission and a retry's seed (the report branches
    # 0161 gave stewardship_delivery_retry_admitted_v1 and the retry render)
    # ask whether the recipient is still one of the report's, by its kind,
    # reference and address, as the digests' retry reads did.
    _columns(columns, RECIPIENT, {"kind", "ref", "address"})
