"""SQL authority over the Admin automation tables, per login (ADM-11 PR 2).

The guards in ``schema/migrations/0004_automation_sessions.sql`` decide which
change each login may make; these grants only let each login reach the rows
its own work touches. Every login updates the session table through columns,
never the whole row, so nothing outside the guard's reach is writable.

- The web login (the pages and the host command line, which runs as web)
  approves sessions, opens command logins, records use and endings, records
  notices and acknowledges them.
- The general worker runs the maintenance task: Admin session cleanup, the
  endings that role loss, removal and recovery imply, and incident
  resolution. ``stewardship_automation_live_v1`` and
  ``stewardship_export_authorized_v1`` run with its rights, so it reads the
  rule and user rows they read.
- The offline admin-recovery login ends every session during a restore.
"""

# Columns an ending writes (revoked_at with its reason and attribution). The
# mutable guard sets updated_at itself, but Django's UPDATE names it.
END_COLUMNS = frozenset(
    {"revoked_at", "end_reason", "version", "updated_at", "actor_id", "correlation_id"}
)
# Django returns a database default (created_at) after an ORM INSERT, which
# needs SELECT on that column.
NOTICE_RETURNING = frozenset({"created_at"})


def add_automation_web_grants(tables, columns):
    """Web: approval, command logins, use, endings, notices and acknowledgements."""
    for table in (
        "stewardship_automation_session",
        "stewardship_automation_login",
        "stewardship_automation_notice",
        "stewardship_automation_notice_ack",
    ):
        tables[table] = {"SELECT", "INSERT"}
    columns["stewardship_automation_session"] = {
        "UPDATE": set(END_COLUMNS) | {"last_used_at"}
    }


def add_automation_worker_grants(tables, columns):
    """Worker: the maintenance task's cleanup, endings and incident resolution.

    ``cleanup_admin_sessions`` reads the session columns it needs, revokes an
    ended row (``revoked_at`` with a version bump) and deletes it; its Django
    session goes through ``stewardship_admin_session_purge_v1`` (a
    ``runtime_functions`` grant), so the worker never reads a session key or
    session data. The liveness check reads the current rules, the portal
    user's address and status (already granted) and recovery records. The
    worker already holds the operational incident grants that resolving an
    automation episode needs.
    """
    sessions = columns.setdefault("stewardship_portal_session", {})
    sessions.setdefault("SELECT", set()).update(
        {
            "id",
            "principal_id",
            "authenticated_at",
            "last_activity_at",
            "expires_at",
            "revoked_at",
            "version",
        }
    )
    sessions.setdefault("UPDATE", set()).update({"revoked_at", "version"})
    tables.setdefault("stewardship_portal_session", set()).add("DELETE")
    for table in (
        "stewardship_address_rule",
        "stewardship_domain_rule",
        "stewardship_admin_revocation",
    ):
        tables.setdefault(table, set()).add("SELECT")
    tables["stewardship_automation_login"] = {"SELECT", "DELETE"}
    tables["stewardship_automation_session"] = {"SELECT"}
    columns["stewardship_automation_session"] = {"UPDATE": set(END_COLUMNS)}
    tables["stewardship_automation_notice"] = {"INSERT"}
    columns["stewardship_automation_notice"] = {"SELECT": set(NOTICE_RETURNING)}


def add_automation_recovery_grants(tables, columns):
    """Admin recovery: ``revoke-automation-sessions`` ends every live session.

    Offline logins had no column grants before this; ``offline_grants``
    returns these column maps and offline admission verifies them. The
    command writes its notices and audit events with plain INSERTs, so it
    needs no SELECT on them.
    """
    tables["stewardship_automation_session"] = {"SELECT"}
    columns["stewardship_automation_session"] = {"UPDATE": set(END_COLUMNS)}
    tables["stewardship_automation_notice"] = {"INSERT"}
