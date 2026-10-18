"""Ministry leader email table grants (#1592), by login.

- Web records an Administrator's request (its guard fills in the facts) and
  reads, for the Users page's Last emailed column, the requests and each
  recipient row's address, cohort and outbox message, never the compiled
  content.
- The scheduler finds requests no preparation Task owns and follows the
  engine's opaque cohort and recipient bindings, never an address.
- The general worker reads the request it prepares and writes the cohort and
  the recipient rows (subject and bodies included).
- Mail dispatch reads the cohort and the recipient rows, whose kept content
  is what it sends.

Every write is admitted by the tables' own guards (migration 0117).
"""

# The columns the scheduler and web need to bind or list a recipient row
# without its compiled content.
RECIPIENT_BINDING = {"id", "cohort_id", "outbox_id"}
COHORT_BINDING = {"id", "request_id", "run_id", "recipient_count"}


def _columns(columns, table, privilege, names):
    """Add column grants without dropping earlier ones."""
    columns.setdefault(table, {}).setdefault(privilege, set()).update(names)


def add_leader_email_task_grants(tables, columns, *, worker):
    """The worker prepares requests; the scheduler only sees their bindings."""
    if worker:
        tables.setdefault("stewardship_leader_email_request", set()).add("SELECT")
        for table in (
            "stewardship_leader_email_cohort",
            "stewardship_leader_email_recipient",
        ):
            tables.setdefault(table, set()).update({"SELECT", "INSERT"})
        return
    _columns(
        columns, "stewardship_leader_email_request", "SELECT", {"id", "created_at"}
    )
    _columns(columns, "stewardship_leader_email_cohort", "SELECT", COHORT_BINDING)
    _columns(columns, "stewardship_leader_email_recipient", "SELECT", RECIPIENT_BINDING)


def add_leader_email_dispatch_grants(tables):
    """Mail dispatch sends each recipient row's kept content to its address."""
    for table in (
        "stewardship_leader_email_cohort",
        "stewardship_leader_email_recipient",
    ):
        tables.setdefault(table, set()).add("SELECT")


def add_leader_email_web_grants(tables, columns):
    """Record a request; list each address's latest email, never its content."""
    tables.setdefault("stewardship_leader_email_request", set()).update(
        {"SELECT", "INSERT"}
    )
    _columns(
        columns,
        "stewardship_leader_email_recipient",
        "SELECT",
        RECIPIENT_BINDING | {"address", "created_at"},
    )
    _columns(columns, "stewardship_leader_email_cohort", "SELECT", COHORT_BINDING)
