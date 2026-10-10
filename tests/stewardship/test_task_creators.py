"""Every task-creating call site is pinned to its task types and login (#389).

``stewardship_task_type_creators_v1`` (frozen migration 0041) lets only the
logins listed for a task type INSERT it. A wrong entry would make a
Production service fail to queue work, so the map is derived here from the
code and checked both ways:

- ``jobs.storage.enqueue`` and ``retry_failed`` are the only Python code that
  builds a ``TaskRun``. Every call to them is found by AST and must have an
  entry in ``SITES`` naming its task types, the login(s) its process runs
  as, and the modules that import its enclosing function. A new or moved
  call site, or a new importer (a possible new process), fails until the
  entry is reviewed.
- The union of ``SITES`` must equal the SQL creator map exactly, and the
  creator map must list exactly the task types the execution map
  (``stewardship_task_type_login_v1``) knows, so a new type without a
  creator entry fails.

The only SQL that inserts a task is the SECURITY DEFINER
``stewardship_production_confirmation_effect_v1``; it runs as the schema
owner, which the guard exempts, so it needs no creator entry.

Logins: ``web`` covers the Admin and Family pages, the Admin command line
(which runs as the web login) and the LOCAL seeder's web entry points;
``scheduler`` covers the producers ``runtime_process`` compiles; ``worker``
covers the general and source worker handlers.
"""

import ast
import re
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/parishkit/stewardship"
SCHEMA = PACKAGE / "schema"
WEB, SCHEDULER, WORKER = "web", "scheduler", "worker"
LOGINS = {
    WEB: "pk_stewardship_web",
    SCHEDULER: "pk_stewardship_scheduler",
    WORKER: "pk_stewardship_worker",
}
CREATORS = {"enqueue", "retry_failed"}
OWNER_ONLY_SQL = {"stewardship_production_confirmation_effect_v1"}


class Site(NamedTuple):
    """One creating call site: what it creates, as whom, and its importers."""

    types: frozenset
    logins: frozenset
    importers: frozenset


def site(types, logins, importers=()):
    """Build a ``Site`` from plain iterables (a single string is one item)."""

    def as_set(value):
        """One string is one item; anything else is an iterable of items."""
        return frozenset([value] if isinstance(value, str) else value)

    return Site(as_set(types), as_set(logins), as_set(importers))


# "path::qualname" of the function holding the enqueue/retry_failed call.
SITES = {
    # --- Admin and Family pages, and the Admin command line (web) ---------
    "accounts/activation_progress.py::control": site(
        {"production_tokens", "production_token_cleanup"},
        WEB,
        {"accounts/activation_views.py", "local/seed_web.py"},
    ),
    "accounts/campaign_family_test.py::_enqueue": site(
        # Called by request_tests (the Family test page and admin_tests).
        "family_mail_test",
        WEB,
    ),
    "accounts/campaign_mail.py::request_sample": site(
        "campaign_mail_test",
        WEB,
        {
            "accounts/campaign_mail_views.py",
            "admin_tests.py",
            "local/seed_web.py",
        },
    ),
    "accounts/confirmation_progress.py::retry": site(
        "activation_catchup", WEB, {"accounts/confirmation_views.py"}
    ),
    "accounts/setup_mail.py::request_sample": site(
        "setup_mail_test",
        WEB,
        {"accounts/setup_mail_views.py", "local/seed_web.py"},
    ),
    "accounts/setup_source.py::start_source_load": site(
        "setup_source_load",
        WEB,
        {"accounts/setup_views.py", "local/seed_web.py"},
    ),
    "campaigns/cleanup_requests.py::retry_cleanup": site(
        "production_cleanup", WEB, {"accounts/go_live_progress.py"}
    ),
    "campaigns/production_storage.py::begin_transition": site(
        # cleanup_requests.begin_cleanup, from the go-live cleanup page.
        "production_cleanup",
        WEB,
        {"campaigns/cleanup_requests.py"},
    ),
    "jobs/delivery_resolution.py::resolve_delivery": site(
        "outbox_delivery",
        WEB,
        {"admin_operations.py", "jobs/delivery_bulk.py", "jobs/delivery_views.py"},
    ),
    "jobs/family_mail_tasks.py::retry_preparation": site(
        "family_mail_prepare", WEB, {"jobs/task_retries.py"}
    ),
    "reports/digest_retry.py::retry_digest": site(
        {
            "daily_digest_prepare",
            "daily_digest_finalize",
            "weekly_digest_prepare",
            "weekly_digest_finalize",
        },
        WEB,
        {"jobs/task_retries.py"},
    ),
    "reports/directory_exports.py::create_directory_export": site(
        "report_export",
        WEB,
        {
            "admin_family_exports.py",
            "reports/directory_export_views.py",
            "reports/export_services.py",
        },
    ),
    "reports/exact_services.py::create_exact_export": site(
        "report_exact_export",
        WEB,
        {"reports/exact_ui.py"},
    ),
    "reports/exact_services.py::retry_exact_export": site(
        "report_exact_export",
        WEB,
        {"reports/exact_ui.py"},
    ),
    "reports/export_cleanup.py::retry_cleanup": site(
        "report_export_cleanup", WEB, {"jobs/task_retries.py"}
    ),
    "reports/export_services.py::create_export": site(
        "report_export",
        WEB,
        {"admin_exports.py", "reports/export_ui.py"},
    ),
    "reports/export_services.py::retry_export": site(
        "report_export",
        WEB,
        {
            "admin_exports.py",
            "reports/exact_services.py",
            "reports/export_ui.py",
        },
    ),
    "reports/family_test_names.py::create_family_test_names_export": site(
        "report_export",
        WEB,
        {"admin_tests.py", "reports/export_services.py"},
    ),
    "reports/financial_exports.py::create_financial_export": site(
        "report_export",
        WEB,
        {
            "admin_family_exports.py",
            "reports/export_services.py",
            "reports/financial_export_views.py",
        },
    ),
    "reports/information_exports.py::create_information_export": site(
        "report_export",
        WEB,
        {
            "admin_family_exports.py",
            "reports/export_services.py",
            "reports/information_export_views.py",
        },
    ),
    "reports/ministry_exports.py::create_ministry_export": site(
        "report_export",
        WEB,
        {
            "admin_family_exports.py",
            "reports/export_services.py",
            "reports/ministry_export_views.py",
        },
    ),
    "reports/timeline_exports.py::create_timeline_export": site(
        # The Family timeline page's export form, regenerate and
        # ``export family-timeline`` (ADM-11 PR 8g).
        "report_export",
        WEB,
        {
            "admin_family_exports.py",
            "reports/export_services.py",
            "reports/family_timeline_views.py",
        },
    ),
    "reports/weekly_manual.py::request_manual_report": site(
        "weekly_digest_prepare",
        WEB,
        {"admin_digests.py", "reports/weekly_manual_views.py"},
    ),
    "responses/receipts.py::create_submission_receipt": site(
        # A Family's submission receipt.
        "outbox_delivery",
        WEB,
        {"responses/submission.py"},
    ),
    # --- Shared by several processes --------------------------------------
    "campaigns/activation_cleanup.py::request_cancellation": site(
        # The links page (web) and the go-live producer (scheduler).
        "production_token_cleanup",
        {WEB, SCHEDULER},
        {"accounts/activation_progress.py", "campaigns/go_live_sequencing.py"},
    ),
    "campaigns/activation_tokens.py::request_preparation": site(
        # The links page (web) and the go-live producer (scheduler).
        "production_tokens",
        {WEB, SCHEDULER},
        {"accounts/activation_progress.py", "campaigns/go_live_sequencing.py"},
    ),
    "source/requests.py::request_refresh": site(
        # The refresh page and command line (web), the refresh and go-live
        # producers (scheduler), and the source handler's full fallback
        # after a rejected delta (worker).
        "source_refresh",
        {WEB, SCHEDULER, WORKER},
        {
            "accounts/refresh_views.py",
            "campaigns/go_live_sequencing.py",
            "local/seed_web.py",
            "source/fallback.py",
            "source/production.py",
        },
    ),
    # --- Scheduler producers (runtime_process) ------------------------------
    "accounts/automation_maintenance.py::MaintenanceProducer.__call__": site(
        "automation_maintenance", SCHEDULER, {"runtime_process.py"}
    ),
    "accounts/branding_cleanup.py::produce_cleanup": site(
        "branding_cleanup", SCHEDULER, {"runtime_process.py"}
    ),
    "campaigns/boundary_production.py::produce_boundaries": site(
        "campaign_boundary", SCHEDULER, {"runtime_process.py"}
    ),
    "jobs/family_mail_tasks.py::enqueue_preparation": site(
        # FamilyScheduleProducer's _enqueue.
        "family_mail_prepare",
        SCHEDULER,
        {"campaigns/schedule_production.py"},
    ),
    "jobs/operational_collection.py::produce_collection": site(
        "operational_collect", SCHEDULER, {"runtime_process.py"}
    ),
    "jobs/operational_fanout.py::produce_fanout": site(
        # Once for the operational owner and once for SECURITY.
        {"operational_prepare", "security_prepare"},
        SCHEDULER,
        # operational_owner imports the module, not the producer.
        {"jobs/operational_owner.py", "runtime_process.py"},
    ),
    "jobs/operational_slack_tasks.py::produce_slack": site(
        "operational_slack", SCHEDULER, {"runtime_process.py"}
    ),
    "reports/digest_finalization.py::DigestFinalizeProducer.__call__": site(
        # Its Daily and Weekly subclasses are runtime_process's producers.
        {"daily_digest_finalize", "weekly_digest_finalize"},
        SCHEDULER,
    ),
    "reports/digest_ownership.py::DailyDigestProducer.__call__": site(
        "daily_digest_prepare", SCHEDULER, {"runtime_process.py"}
    ),
    "reports/export_cleanup.py::produce_cleanup": site(
        "report_export_cleanup", SCHEDULER, {"runtime_process.py"}
    ),
    "reports/fact_production.py::produce_facts": site(
        "report_facts", SCHEDULER, {"runtime_process.py"}
    ),
    "reports/verification_production.py::produce_verifications": site(
        "report_fact_verification", SCHEDULER, {"runtime_process.py"}
    ),
    "reports/weekly_ownership.py::WeeklyDigestProducer.__call__": site(
        "weekly_digest_prepare", SCHEDULER, {"runtime_process.py"}
    ),
    "source/setup_cleanup.py::produce_setup_cleanup": site(
        "setup_source_cleanup", SCHEDULER, {"runtime_process.py"}
    ),
    "source/setup_final_tasks.py::enqueue_finalization": site(
        # setup_final_production.produce_finalization.
        "setup_finalize",
        SCHEDULER,
        {"source/setup_final_production.py"},
    ),
    # --- Worker handlers ----------------------------------------------------
    "jobs/outbox_storage.py::create_message": site(
        # Family preparation and test, operational and security alert
        # fan-out, and daily and weekly digest fan-out handlers.
        "outbox_delivery",
        WORKER,
        {
            "jobs/family_mail_preparation.py",
            "jobs/family_mail_test_tasks.py",
            "jobs/operational_fanout.py",
            "reports/digest_fanout.py",
            "reports/weekly_fanout.py",
        },
    ),
    "reports/exact_tasks.py::_handoff": site(
        # The exact-export handler hands its renderer a report_export.
        "report_export",
        WORKER,
    ),
    # --- No runtime caller -------------------------------------------------
    "campaigns/catchup_allocation.py::allocate_activation": site(
        # campaigns.runtime._emit calls it only for ACTIVATE, which only
        # transition_campaign passes, and nothing but tests calls that.
        # Production activation inserts its catch-up through the definer
        # stewardship_production_confirmation_effect_v1 (schema owner).
        "activation_catchup",
        (),
        {"campaigns/runtime.py"},
    ),
}


def _module(rel):
    """The dotted module name of a package-relative path."""
    parts = rel.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["parishkit", "stewardship", *parts])


def _sources():
    """Parse every non-migration package module, keyed by relative path."""
    return {
        path.relative_to(PACKAGE).as_posix(): ast.parse(path.read_text())
        for path in sorted(PACKAGE.rglob("*.py"))
        if "migrations" not in path.relative_to(PACKAGE).parts
    }


def _call_name(node):
    """The called name of ``f(...)`` or ``x.f(...)``, else None."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def creating_sites(sources):
    """Map "path::qualname" to the enqueue/retry_failed calls it holds."""
    found = {}

    def walk(rel, node, stack):
        """Descend, tracking enclosing class and function names."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                walk(rel, child, [*stack, child.name])
                continue
            if isinstance(child, ast.Call) and _call_name(child) in CREATORS:
                found.setdefault(f"{rel}::{'.'.join(stack)}", []).append(child)
            walk(rel, child, stack)

    for rel, tree in sources.items():
        if rel != "jobs/storage.py":
            walk(rel, tree, [])
    return found


def _absolute(rel, node):
    """The absolute module an ``ImportFrom`` in ``rel`` names."""
    if not node.level:
        return node.module or ""
    package = _module(rel).split(".")
    if not rel.endswith("__init__.py"):
        package.pop()
    package = package[: len(package) - (node.level - 1)]
    return ".".join([*package, *([node.module] if node.module else [])])


def importers(sources, rel, name):
    """Modules importing ``name`` from ``rel``, or ``rel`` itself as a module."""
    target = _module(rel)
    result = set()
    for other, tree in sources.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                base = _absolute(other, node)
                if any(
                    (base == target and alias.name == name)
                    or f"{base}.{alias.name}" == target
                    for alias in node.names
                ):
                    result.add(other)
            elif isinstance(node, ast.Import) and any(
                alias.name == target for alias in node.names
            ):
                result.add(other)
    return result


def _constants(sources, rel, seen=()):
    """Module-level string constants of ``rel``, following ``from`` imports."""
    values = {}
    for node in sources[rel].body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            values[node.targets[0].id] = node.value.value
        elif isinstance(node, ast.ImportFrom):
            source = _absolute(rel, node).removeprefix("parishkit.stewardship.")
            path = source.replace(".", "/") + ".py"
            if path in sources and path not in seen:
                imported = _constants(sources, path, (*seen, rel))
                for alias in node.names:
                    if alias.name in imported:
                        values[alias.asname or alias.name] = imported[alias.name]
    return values


def resolved_types(sources, rel, calls):
    """The literal task types an ``enqueue(task_type=...)`` names, if static."""
    constants = _constants(sources, rel)
    types = set()
    for call in calls:
        for keyword in call.keywords:
            if keyword.arg != "task_type":
                continue
            if isinstance(keyword.value, ast.Constant):
                types.add(keyword.value.value)
            elif isinstance(keyword.value, ast.Name) and keyword.value.id in constants:
                types.add(constants[keyword.value.id])
    return types


def _function_body(text, name, keyword):
    """The text of ``keyword public.name(`` up to its closing ``$$;``."""
    start = text.index(f"{keyword} public.{name}(")
    return text[start : text.index("$$;", start)]


def sql_executors():
    """The execution map: task type to its executing login (functions.sql)."""
    body = _function_body(
        (SCHEMA / "functions.sql").read_text(),
        "stewardship_task_type_login_v1",
        "CREATE FUNCTION",
    )
    return {
        name: login
        for names, login in re.findall(
            r"WHEN task_type IN \((.*?)\)\s*THEN '(\w+)'", body, re.DOTALL
        )
        for name in re.findall(r"'(\w+)'", names)
    }


def sql_creators():
    """The creator map from the latest frozen file that defines it."""
    (path,) = [
        path
        for path in sorted((SCHEMA / "migrations").glob("*.sql"))
        if "FUNCTION public.stewardship_task_type_creators_v1(" in path.read_text()
    ][-1:]
    text = path.read_text()
    keyword = (
        "CREATE OR REPLACE FUNCTION"
        if "CREATE OR REPLACE FUNCTION public.stewardship_task_type_creators_v1("
        in text
        else "CREATE FUNCTION"
    )
    body = _function_body(text, "stewardship_task_type_creators_v1", keyword)
    return {
        name: frozenset(re.findall(r"'(\w+)'", logins))
        for name, logins in re.findall(r"WHEN '(\w+)' THEN ARRAY\[(.*?)\]", body)
    }


def derived_creators():
    """The creator map ``SITES`` implies, in SQL login names."""
    result = {}
    for entry in SITES.values():
        for task_type in entry.types:
            result.setdefault(task_type, set()).update(
                LOGINS[login] for login in entry.logins
            )
    return {name: frozenset(logins) for name, logins in result.items()}


def test_every_creating_call_site_has_a_reviewed_entry():
    """A new, moved or removed enqueue/retry_failed call fails until reviewed."""
    found = creating_sites(_sources())
    assert sorted(found) == sorted(SITES)


def test_each_site_lists_exactly_its_importers():
    """A new importer may be a new process; its login must be reviewed."""
    sources = _sources()
    for key, entry in SITES.items():
        rel, qualname = key.split("::")
        name = qualname.split(".")[0]
        assert importers(sources, rel, name) == entry.importers, key


def test_statically_named_task_types_match_their_entries():
    """Where the call names its type as a literal or constant, it matches."""
    sources = _sources()
    for key, calls in creating_sites(sources).items():
        rel = key.split("::")[0]
        named = resolved_types(sources, rel, calls)
        assert named <= SITES[key].types, key
    # The resolution works: most sites name their type statically.
    resolved = sum(
        bool(resolved_types(sources, key.split("::")[0], calls))
        for key, calls in creating_sites(sources).items()
    )
    assert resolved >= 30


def test_only_task_storage_builds_task_rows():
    """``TaskRun`` is built only in ``jobs/storage.py``; no raw SQL inserts it."""
    insert = re.compile(r"INSERT\s+INTO\s+(public\.)?stewardship_task_run\b", re.I)
    for rel, tree in _sources().items():
        for node in ast.walk(tree):
            if rel != "jobs/storage.py" and isinstance(node, ast.Call):
                func = node.func
                assert not (isinstance(func, ast.Name) and func.id == "TaskRun"), rel
                assert not (
                    isinstance(func, ast.Attribute)
                    and func.attr
                    in {"create", "bulk_create", "get_or_create", "update_or_create"}
                    and "TaskRun" in ast.unparse(func.value)
                ), rel
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not insert.search(node.value), rel


def test_only_the_definer_confirmation_effect_inserts_tasks_in_sql():
    """SQL task inserts run as the schema owner, which the guard exempts."""
    insert = re.compile(r"INSERT\s+INTO\s+(public\.)?stewardship_task_run\b", re.I)
    found = set()
    for path in [*SCHEMA.glob("*.sql"), *(SCHEMA / "migrations").glob("*.sql")]:
        text = path.read_text()
        for match in insert.finditer(text):
            # The INSERT belongs to the last function defined before it.
            name = re.findall(r"FUNCTION public\.(\w+)\(", text[: match.start()])[-1]
            found.add(name)
            definer = re.compile(
                rf"FUNCTION public\.{name}\(\)[^$]*?SECURITY DEFINER", re.S
            )
            assert definer.search(text), name
    assert found == OWNER_ONLY_SQL


def test_sql_creator_map_equals_the_derived_map():
    """No login may create more, or less, than its code does."""
    assert sql_creators() == derived_creators()


def test_every_executed_task_type_has_a_creator():
    """A new task type without a creator entry fails here."""
    creators = sql_creators()
    assert set(creators) == set(sql_executors())
    assert all(creators.values())
    # Mail dispatch only executes; it creates nothing.
    assert "pk_stewardship_mail_dispatch" not in set().union(*creators.values())


def test_scans_find_known_shapes():
    """The site scan and importer scan see methods, nested and relative forms."""
    sources = {
        "a/one.py": ast.parse(
            "from ..jobs.storage import enqueue\n"
            "class P:\n"
            "    def __call__(self):\n"
            "        enqueue(task_type='x')\n"
            "def outer():\n"
            "    def inner():\n"
            "        storage.retry_failed(run_id=1)\n"
        ),
        "a/two.py": ast.parse("from .one import outer\n"),
        "b/three.py": ast.parse("from parishkit.stewardship.a import one\n"),
    }
    found = creating_sites(sources)
    assert sorted(found) == ["a/one.py::P.__call__", "a/one.py::outer.inner"]
    assert resolved_types(sources, "a/one.py", found["a/one.py::P.__call__"]) == {"x"}
    assert importers(sources, "a/one.py", "outer") == {"a/two.py", "b/three.py"}
