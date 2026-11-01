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

Send a test to me (frozen file 0173, #2036): web records a test with the
email it compiled; the worker reads it to make the test's message and mail
dispatch to send it; the scheduler and web see only its bindings. No login
updates it: the database settles and scrubs it itself.
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
# Send a test to me (0173, #2036).
TEST = "stewardship_report_email_test"
TABLES = (MANUAL, PREPARATION, SNAPSHOT, RECIPIENT, COMPLETION, DECISION, TEST)
# The columns that bind a snapshot or recipient without its content.
# When its email was made is a binding too: Report emails lists the sends.
SNAPSHOT_BINDING = {"id", "preparation_id", "campaign_id", "has_content", "created_at"}
RECIPIENT_BINDING = {"id", "snapshot_id", "outbox_id", "disposition"}
# A test's columns without its address or email (the inserted row's
# database defaults included, which an INSERT reads back).
TEST_BINDING = {
    "id",
    "created_at",
    "updated_at",
    "version",
    "actor_id",
    "correlation_id",
    "campaign_id",
    "configuration_id",
    "definition_id",
    "requested_by_id",
    "mode",
    "testing_recipient",
    "task_id",
    "state",
}


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
        # A test task's hint admission reads the test's bindings.
        _columns(columns, TEST, TEST_BINDING)
        return
    for table in (SNAPSHOT, RECIPIENT):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    # The test task makes its message from the kept email (#2036).
    tables.setdefault(TEST, set()).add("SELECT")
    # The coverage comparison (stewardship_report_email_prior_v1), which the
    # fanout and the recipient guard run with the worker's rights, reads
    # the Administrator's coverage decisions (#1872).
    tables.setdefault(DECISION, set()).add("SELECT")
    # The open-requests read (and the snapshot guard, which runs it with the
    # worker's rights) tells open from followed-up requests, and, since v2
    # (frozen file 0196, #2331), To handle from To review and Ignored by the
    # follow-up flag and the latest Staff revision's decision.
    _columns(
        columns,
        "stewardship_additional_information",
        {"followed_up_at", "follow_up_needed"},
    )
    _columns(
        columns,
        "stewardship_information_revision",
        {"item_id", "expected_version", "review_decision"},
    )


def add_report_email_dispatch_grants(tables, columns, *, private):
    """Mail dispatch reads the kept email; scheduler recovery sees bindings."""
    for table in (PREPARATION, COMPLETION):
        tables.setdefault(table, set()).add("SELECT")
    if private:
        # A test's kept email is what its message sends (#2036).
        for table in (SNAPSHOT, RECIPIENT, TEST):
            tables.setdefault(table, set()).add("SELECT")
        return
    _columns(columns, SNAPSHOT, SNAPSHOT_BINDING)
    _columns(columns, RECIPIENT, RECIPIENT_BINDING)
    _columns(columns, TEST, TEST_BINDING)


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
    """Record Send now, tests and coverage decisions; read sends and copies."""
    tables.setdefault(MANUAL, set()).update({"SELECT", "INSERT"})
    tables.setdefault(DECISION, set()).update({"SELECT", "INSERT"})
    # Send a test to me (#2036): web records the test and reads its
    # bindings (the one-pending check), never its email again.
    tables.setdefault(TEST, set()).add("INSERT")
    add_report_email_dispatch_grants(tables, columns, private=False)
    _columns(columns, SNAPSHOT, COPY_COLUMNS)
    # Sent mail's retry admission and a retry's seed (the report branches
    # 0161 gave stewardship_delivery_retry_admitted_v1 and the retry render)
    # ask whether the recipient is still one of the report's, by its kind,
    # reference and address, as the digests' retry reads did.
    _columns(columns, RECIPIENT, {"kind", "ref", "address"})
