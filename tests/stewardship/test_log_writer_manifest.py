"""Every operational log write path is on its login's writer allow-list (#389 L2).

``stewardship_operational_log_writer_v1`` gives each runtime login a closed
(schema, event, level) list. Most ``operational()`` writes run in their
caller's main transaction, so an entry missing from the list would abort
real work rather than lose one log line. This test keeps the two in step:

- ``WRITERS`` is the reviewed manifest of every ``operational()`` call site
  (per file, in source order) with the logins that run it and the schema,
  events and levels it can write. A new call site, or one whose literal
  schema, event or level leaves its manifest entry, fails here.
- ``RAW_WRITERS`` covers the files that INSERT rows directly, and the SQL
  invoker triggers that write as a runtime login.
- Every manifest entry must be admitted by the trigger body in
  ``schema/functions.sql`` (the copy migration 0035 installs).
"""

import ast
import re
from itertools import product

from parishkit.stewardship.observability import Event

from .test_log_contract import PACKAGE, SCHEMA, _calls, _events, _literal_level

E = Event
TIMEOUTS = {
    E.TASK_TIMED_OUT,
    E.HELPER_TIMED_OUT,
    E.WORK_BUDGET_REACHED,
    E.TASK_LEASE_LOST,
}
# What source refresh, setup load and setup finalize decide (source/failures.py).
SOURCE_DECISIONS = {
    E.SOURCE_TENANT_MISMATCH,
    E.SOURCE_DESTRUCTIVE_CHANGE,
    E.SOURCE_PROVIDER_FAILED,
    E.SOURCE_INVALID,
    E.SOURCE_HELD,
    E.SOURCE_CREDENTIAL_FAILED,
}

# path -> [(logins, schema, events, levels)], one per operational() call in
# source order. ``events`` is a set (each at every level listed) or a mapping
# of event to its own levels when the pairing is fixed.
WRITERS = {
    "campaigns/engagement.py": [
        ({"web"}, "failure", {E.FAMILY_ENGAGEMENT_FAILED}, {"ERROR"})
    ],
    "jobs/backup_health.py": [
        ({"worker"}, "exception", {E.CONFIG_MISMATCH}, {"WARNING"})
    ],
    # HEALTH_CHECKS: the source check is CRITICAL, the others ERROR.
    "jobs/operational_collection.py": [
        (
            {"worker"},
            "failure",
            {E.TASK_FAILED: {"ERROR"}, E.SOURCE_INVALID: {"CRITICAL"}},
            {"ERROR", "CRITICAL"},
        )
    ],
    "reports/export_cleanup.py": [
        ({"worker"}, "failure", {E.TASK_FAILED}, {"CRITICAL"})
    ],
    "reports/verification_tasks.py": [
        ({"worker"}, "failure", {E.TASK_FAILED}, {"CRITICAL"}),
        ({"worker"}, "task", {E.FACT_DRIFT}, {"CRITICAL"}),
    ],
    # Called from the Family form (web) and from promotion (worker).
    "responses/diagnostics.py": [
        ({"web", "worker"}, "member_source", {E.SOURCE_MEMBER_UNUSABLE}, {"WARNING"})
    ],
    "source/compaction.py": [
        ({"worker"}, "failure", {E.SOURCE_RETENTION_SKIPPED}, {"ERROR"})
    ],
    "source/failures.py": [
        ({"worker"}, "failure", SOURCE_DECISIONS, {"INFO", "WARNING", "CRITICAL"})
    ],
    "source/health.py": [
        (
            {"worker"},
            "failure",
            {E.SOURCE_TENANT_MISMATCH, E.SOURCE_INVALID},
            {"CRITICAL"},
        )
    ],
    "source/production.py": [({"scheduler"}, "schedule", {E.SOURCE_HELD}, {"INFO"})],
    "source/setup_execution.py": [
        ({"worker"}, "failure", SOURCE_DECISIONS, {"INFO", "CRITICAL"})
    ],
    "source/setup_final_execution.py": [
        ({"worker"}, "failure", SOURCE_DECISIONS, {"INFO", "CRITICAL"})
    ],
}

RUNTIME = {"web", "worker", "scheduler", "mail_dispatch", "backup_worker"}
# Direct INSERTs, by file, and SQL invoker triggers that run as a runtime
# login. Event names here are strings: #787's web_unhealthy is not on main yet.
RAW_WRITERS = {
    # record_timeout: every runtime login's time-limit entries.
    "audit/timeouts.py": [
        (RUNTIME, "timeout", {e.value for e in TIMEOUTS}, {"INFO", "WARNING", "ERROR"})
    ],
    "web_supervisor.py": [({"web"}, "timeout", {"helper_timed_out"}, {"ERROR"})],
    "campaigns/boundary_health.py": [
        ({"scheduler"}, "due_work", {"campaign_boundary_lag"}, {"WARNING"})
    ],
    # The due-work health trigger (an invoker, fired by the scheduler).
    "schema:stewardship_due_work_health_v1": [
        ({"scheduler"}, "due_work", {"due_work_lag"}, {"CRITICAL"})
    ],
    # #787's web health trigger (an invoker, fired by the scheduler).
    "schema:web_health": [({"scheduler"}, "failure", {"web_unhealthy"}, {"CRITICAL"})],
}


def _schema(node):
    """A call's ``schema=`` ContextKind value, or None when not a literal."""
    for keyword in node.keywords:
        if keyword.arg == "schema" and isinstance(keyword.value, ast.Attribute):
            from parishkit.stewardship.audit.schemas import ContextKind

            return ContextKind[keyword.value.attr].value
    return None


def _levels(node):
    """The levels a call's ``level=`` can name, or None when it is computed.

    A conditional of two literals counts as both, as in the setup writers.
    """
    level = _literal_level(node)
    if level == "DEFAULT":
        return {"INFO"}
    if level is not None:
        return {level}
    value = next(k.value for k in node.keywords if k.arg == "level")
    if (
        isinstance(value, ast.IfExp)
        and isinstance(value.body, ast.Constant)
        and isinstance(value.orelse, ast.Constant)
    ):
        return {value.body.value, value.orelse.value}
    return None


def _writer_body():
    """The writer trigger's body from the fresh-install functions file."""
    text = (SCHEMA / "functions.sql").read_text(encoding="utf-8")
    return re.search(
        r"^CREATE FUNCTION public\.stewardship_operational_log_writer_v1\(\)"
        r".*?AS \$\$(.*?)\$\$;$",
        text,
        re.S | re.M,
    ).group(1)


def _quoted(text):
    """The single-quoted words in a SQL list."""
    return set(re.findall(r"'(\w+)'", text))


def _allowed():
    """Every (login, schema, event, level) the trigger body admits."""
    body = re.sub(r"--[^\n]*", "", _writer_body())
    allowed = set()
    # The timeout arm, shared by every runtime login.
    logins, events, levels = re.search(
        r"current_user IN \(([^)]*)\)\s*AND NEW\.schema='timeout'\s*"
        r"AND NEW\.event IN \(([^)]*)\)\s*AND NEW\.level IN \(([^)]*)\)",
        body,
    ).groups()
    for login, event, level in product(
        _quoted(logins), _quoted(events), _quoted(levels)
    ):
        allowed.add((login.removeprefix("pk_stewardship_"), "timeout", event, level))
    # One arm per login: explicit tuples, and schema/event-list/level-list sets.
    arms = re.split(r"current_user='pk_stewardship_(\w+)'", body)
    for login, arm in zip(arms[1::2], arms[2::2], strict=True):
        arm = arm.split("current_user=")[0]
        for schema, event, level in re.findall(r"\('(\w+)','(\w+)','(\w+)'\)", arm):
            allowed.add((login, schema, event, level))
        for schema, events, levels in re.findall(
            r"NEW\.schema='(\w+)'\s*AND NEW\.event IN \(([^)]*)\)\s*"
            r"AND NEW\.level IN \(([^)]*)\)",
            arm,
        ):
            for event, level in product(_quoted(events), _quoted(levels)):
                allowed.add((login, schema, event, level))
    return allowed


def test_every_operational_call_site_is_in_the_manifest():
    """A new call site, or a literal outside its entry, needs review here."""
    found = {}
    for path, node in _calls("operational"):
        if path == "audit/services.py":
            continue
        keyword_event = any(k.arg == "event" for k in node.keywords)
        # A call that names its event by keyword is a write path too; it must
        # name it positionally, as every caller does, so this scan checks it.
        assert node.args or not keyword_event, f"{path}:{node.lineno}"
        if not node.args:
            continue
        found.setdefault(path, []).append(node)
    assert set(found) == set(WRITERS), "update WRITERS for the new call sites"
    for path, nodes in found.items():
        assert len(nodes) == len(WRITERS[path]), path
        for node, (_, schema, events, levels) in zip(nodes, WRITERS[path], strict=True):
            where = f"{path}:{node.lineno}"
            assert _schema(node) in {schema, None}, where
            literal = _events(node.args[0])
            assert literal is None or literal <= set(events), where
            computed = _levels(node)
            assert computed is None or computed <= levels, where


def test_every_raw_insert_file_is_in_the_manifest():
    """A new direct INSERT into the log needs its own reviewed entry."""
    inserting = {
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if "migrations" not in path.parts
        and re.search(
            r"INSERT INTO\s+stewardship_operational_log", path.read_text("utf-8")
        )
    }
    assert inserting == {name for name in RAW_WRITERS if not name.startswith("schema:")}


def test_the_writer_admits_every_manifest_entry():
    """The trigger body admits every write path the manifest lists."""
    allowed = _allowed()
    entries = [entry for entries in WRITERS.values() for entry in entries]
    entries += [entry for entries in RAW_WRITERS.values() for entry in entries]
    missing = set()
    for logins, schema, events, levels in entries:
        pairs = (
            events.items()
            if isinstance(events, dict)
            else [(event, levels) for event in events]
        )
        for event, event_levels in pairs:
            name = getattr(event, "value", event)
            for login, level in product(logins, event_levels):
                if (login, schema, name, level) not in allowed:
                    missing.add((login, schema, name, level))
    assert not missing, sorted(missing)


def test_web_may_never_write_critical():
    """CRITICAL pages and opens incidents; web has no path that needs it."""
    assert not {
        item for item in _allowed() if item[0] == "web" and item[3] == "CRITICAL"
    }
