"""Deterministic, complete test partitions for isolated CI runners."""

import hashlib
from pathlib import Path

BROWSER_ENGINES = ("chromium", "firefox", "webkit")

# The browser CI jobs (#627), one workflow matrix entry each; every entry
# runs its ENGINE:INDEX/COUNT partitions in turn. test_browser_ci.py requires
# the matrix to equal this and every engine's partitions to appear exactly
# once. Measured before the split (2026-10-06 full runs): one job per engine,
# with test steps of a median 22.6 minutes (WebKit), 14.5 (Firefox) and 7.8
# (Chromium), plus 1-2 minutes of setup. Halving WebKit and Chromium and
# pairing the halves gives three jobs of about 15 minutes of tests each, so
# the WebKit long pole is gone without adding a runner slot.
BROWSER_JOBS = (
    "firefox:1/1",
    "webkit:1/2 chromium:1/2",
    "webkit:2/2 chromium:2/2",
)

# Scheduling hints only: median WebKit seconds per test file, from CI runs
# 37532589327, 37539678473 and 37543609615. Other files weigh
# BROWSER_CASE_SECONDS per case. Hints never select or exclude a test; they
# only balance whole files (which keeps module-scoped fixtures together)
# across an engine's jobs. Engines differ in speed but not much in shape.
BROWSER_FILE_SECONDS = {
    "test_components.py": 235,
    "test_family_pages.py": 60,
    "test_rule_autosave.py": 60,
    "test_about_page.py": 48,
    "test_automation.py": 48,
    "test_family_financial.py": 46,
    "test_family_service.py": 38,
    "test_system_logs.py": 37,
    "test_family_response.py": 36,
    "test_in_place.py": 34,
    "test_table_sorting.py": 32,
    "test_family_acceptance.py": 31,
    "test_send_progress.py": 31,
    "test_followup_in_place.py": 27,
    "test_member_census.py": 26,
    "test_time_entry.py": 24,
    "test_ministry_followup.py": 24,
    "test_content_editor.py": 21,
    "test_family_census.py": 21,
    "test_member_requests.py": 20,
}
BROWSER_CASE_SECONDS = 1.5


def parse_browser_partition(value):
    """Parse a browser job's INDEX/COUNT, such as "2/2"."""
    try:
        index, count = (int(part) for part in value.split("/"))
    except (AttributeError, ValueError):
        raise ValueError("Invalid browser partition") from None
    if not 1 <= index <= count <= 8:
        raise ValueError("Invalid browser partition")
    return (index, count)


def parse_browser_runs(value):
    """Parse one job's space-separated ENGINE:INDEX/COUNT partitions."""
    if type(value) is not str:
        raise ValueError("Invalid browser job")
    runs = []
    for part in value.split():
        engine, _, partition = part.partition(":")
        if engine not in BROWSER_ENGINES or engine in {run[0] for run in runs}:
            raise ValueError("Invalid browser job")
        runs.append((engine, *parse_browser_partition(partition)))
    if not runs:
        raise ValueError("Invalid browser job")
    return runs


def browser_label(engine, index, count):
    """Name a browser job in logs: the engine alone when it is not split."""
    return engine if count == 1 else f"{engine} {index}/{count}"


def browser_partition(cases, engine, index=1, count=1):
    """Assign every browser case by its actual fixture parameter, not its name.

    Require one supported owner for each unique node and all three engines in
    the complete collection. A new unowned case must fail CI rather than vanish
    from every partition. Ordinary local runs do not call this selector.

    With COUNT above one, the engine's cases are further split into COUNT
    disjoint jobs by whole test file: heaviest file first (by the hints
    above), each to the least-loaded job, ties broken by name and job number.
    The split depends only on the collected node IDs, never their order, so
    every job computes the same assignment and together they run every case
    exactly once. A job that would receive no files fails instead.
    """
    if (
        engine not in BROWSER_ENGINES
        or type(cases) is not list
        or not cases
        or any(
            type(case) is not tuple
            or len(case) != 2
            or type(case[0]) is not str
            or not case[0]
            or type(case[1]) is not str
            or case[1] not in BROWSER_ENGINES
            for case in cases
        )
    ):
        raise ValueError("Browser cases require explicit supported engine ownership")
    if len({node for node, _ in cases}) != len(cases):
        raise ValueError("Browser collection contains duplicate cases")
    if {owner for _, owner in cases} != set(BROWSER_ENGINES):
        raise ValueError("Browser collection must exercise every supported engine")
    if type(index) is not int or type(count) is not int or not 1 <= index <= count <= 8:
        raise ValueError("Invalid browser partition")
    files = {}
    for node, owner in cases:
        if owner == engine:
            files.setdefault(node.split("::", 1)[0], []).append(node)
    if len(files) < count:
        raise ValueError("Browser partition would select no tests")

    def weight(path):
        """Recorded seconds for a known file, else a per-case estimate."""
        name = path.rsplit("/", 1)[-1]
        return BROWSER_FILE_SECONDS.get(name, len(files[path]) * BROWSER_CASE_SECONDS)

    loads = [0] * count
    mine = []
    for path in sorted(files, key=lambda path: (-weight(path), path)):
        target = min(range(count), key=lambda job: (loads[job], job))
        loads[target] += weight(path)
        if target == index - 1:
            mine.extend(files[path])
    return sorted(mine)


# Scheduling hints, updated from CI run 35438716036. These never select or
# exclude tests: unknown/new cases receive the default weight. Keep full-duration
# lease/drain checks; distribute their waiting time instead of shortening it.
# One coupling: quality_ci keeps its hang stack dump at least twice the
# largest hint in these maps, so a much slower hint asks for that review.
SLOW_TEST_SECONDS = {
    "test_cancel_cleans_catalog_and_its_final_load_but_keeps_bound_receipts": 100,
    "test_real_finalization_producer_and_compiled_worker": 22,
    "test_failed_helper_drain_keeps_checkpoint_until_real_lease_expiry": 60,
    "test_first_setup_is_atomic_and_has_real_family_codes": 15,
    "test_late_failures_roll_back_every_effect_and_allow_same_live_owner_retry": 16,
    "test_prepared_receipt_does_not_replace_current_consumer_proof": 13,
    "test_final_load_has_new_fences_and_exact_selected_financial_coverage": 15,
    "test_original_cancel_between_final_pages_stops_staging": 10,
    "test_wrong_mounted_key_prevents_final_provider_reads": 10,
    # The existing short fake-provider budget now removes an unrelated wait.
    "test_real_worker_removes_only_expired_setup_rows": 10,
    "test_reference_family_population_does_not_expand_interactive_queries": 50,
    "test_production_lookup_and_sessions_at_reference_population": 70,
    "test_reference_population_capture_is_one_query_and_within_page_budget": 67,
    "test_forced_shutdown_waits_for_real_deadline_then_never_resends": 36,
    "test_blocked_prefix_advances_without_rescanning": 23,
    "test_installed_request_expiry_restores_prior_before_terminal_receipt": 20,
    "test_crash_after_ack_decision_completes_even_after_deadline": 20,
    "test_metrics_expiry_restores_prior_without_persisting_its_hash": 20,
    "test_all_entity_kinds_stage_in_bounded_batches_at_reference_scale": 15,
    "test_reference_confirmation_and_family_submit_during_incomplete_catchup": 121,
}

# Per-test fixture construction is often more expensive than the assertion.
# Rounded module means include setup/call/teardown, excluding each runner's
# first case (which pays session-wide schema creation). Expensive exact cases
# above take precedence. New modules still get the conservative default; these
# hints affect distribution only, never which tests must execute.
MODULE_SECONDS = {
    "test_delivery_closed_postgresql.py": 25,
    "test_go_live_cleanup_postgresql.py": 17,
    "test_mail_health_postgresql.py": 7,
    "test_withdrawal_postgresql.py": 23,
    "test_withdrawal_work_postgresql.py": 21,
    "test_activation_sql_races_postgresql.py": 16,
    "test_activation_views_postgresql.py": 20,
    "test_delivery_control_postgresql.py": 26,
    "test_delivery_digest_recovery_postgresql.py": 23,
    "test_confirmation_readiness_postgresql.py": 23,
    "test_confirmation_guards_postgresql.py": 20,
    "test_confirmation_sql_postgresql.py": 20,
    "test_setup_final_loading_postgresql.py": 7,
    "test_setup_completion_postgresql.py": 14,
    "test_setup_final_execution_postgresql.py": 14,
    "test_daily_digest_resolution_postgresql.py": 5,
    "test_daily_digest_dispatch_postgresql.py": 5,
    "test_daily_digest_schedule_postgresql.py": 6,
    "test_daily_digest_cleanup_postgresql.py": 6,
    "test_setup_credential_installation_postgresql.py": 5,
    "test_security_fanout_postgresql.py": 8,
    "test_security_dispatch_postgresql.py": 8,
    # Three subprocess migrations into two extra databases (issue #487).
    "test_upgrade_parity_postgresql.py": 40,
}

# Only the loaded case retains a real source lease. Do not assign the unloaded
# case its sibling's 90-second expiry cost simply because they share a name.
CASE_SECONDS = {
    "test_cancel_cleans_catalog_and_its_final_load_but_keeps_bound_receipts[False]": 10,
}


def estimated_seconds(nodeid):
    """Estimate execution only; parameter changes remain independently assigned."""
    case = nodeid.rsplit("::", 1)[-1]
    module = nodeid.split("::", 1)[0].rsplit("/", 1)[-1]
    return CASE_SECONDS.get(
        case,
        SLOW_TEST_SECONDS.get(case.split("[", 1)[0], MODULE_SECONDS.get(module, 1)),
    )


def partition(nodeids: list[str], index: int, count: int) -> list[str]:
    """Assign every unique test once, independently of collection ordering."""
    if (
        type(index) is not int
        or type(count) is not int
        or not 1 <= index <= count <= 32
        or len(nodeids) != len(set(nodeids))
    ):
        raise ValueError("Invalid or duplicate CI test partition")
    groups = [[] for _ in range(count)]
    loads = [0] * count
    # Reserve baseline time only for a substantial suite. Small probe suites
    # must not yield an empty shard merely because no real baseline runs there.
    if sum(estimated_seconds(node) for node in nodeids) > count * 180:
        loads[0] = 240
    # Lexical ties repeatedly put the same parameter positions on one runner.
    # A stable digest spreads those unrelated fixture costs without randomizing
    # collection or omitting any node. Return each owner's nodes in normal order.
    for node in sorted(
        nodeids,
        key=lambda node: (
            -estimated_seconds(node),
            hashlib.sha256(node.encode()).digest(),
            node,
        ),
    ):
        target = min(range(count), key=lambda shard: (loads[shard], shard))
        groups[target].append(node)
        loads[target] += estimated_seconds(node)
    return sorted(groups[index - 1])


def tree_digest(root: Path) -> str:
    """Bind evidence to source, tests, dependency locks and validation settings."""
    paths = {root / "pyproject.toml", root / "coverage-stewardship.toml"}
    for directory, pattern in (
        ("src", "*.py"),
        ("src", "*.sql"),
        ("src", "*.html"),
        ("src", "*.css"),
        ("src", "*.js"),
        ("src", "*.svg"),
        ("src", "*.png"),
        ("src", "*.ico"),
        ("src", "*.txt"),
        ("tests", "*.py"),
        ("tests", "*.json"),
        ("tests", "*.yaml"),
        ("tests", "*.toml"),
        ("requirements", "*.txt"),
        (".github/workflows", "*.yml"),
        ("deploy", "*"),
        ("scripts", "*.py"),
        ("tools", "*.py"),
        ("docs", "*.md"),
        ("docs", "*.yaml"),
    ):
        paths.update(
            path
            for path in (root / directory).rglob(pattern)
            if not path.is_dir() and "__pycache__" not in path.parts
        )
    paths.update(root.glob("requirements*.txt"))
    paths.update(
        root / name
        for name in (
            ".dockerignore",
            ".pymarkdown.json",
            "install.py",
            "README.md",
            "CLAUDE.md",
        )
        if (root / name).exists()
    )
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            raise ValueError("CI evidence requires real repository inputs")
        name = path.relative_to(root).as_posix().encode()
        body = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(body).to_bytes(8, "big") + body)
    return digest.hexdigest()
