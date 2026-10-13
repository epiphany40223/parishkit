"""Deterministic, complete test partitions for isolated CI runners."""

import hashlib
from pathlib import Path

BROWSER_ENGINES = ("chromium", "firefox", "webkit")

# The browser CI jobs (#627), one workflow matrix entry each; every entry
# runs its ENGINE:INDEX/COUNT partitions in turn. test_browser_ci.py requires
# the matrix to equal this and every engine's partitions to appear exactly
# once. Measured in October 2026 (#1151), all of an engine's cases take about
# 3,340 s of tests on WebKit, 2,130 on Chromium and 1,570 to 1,750 on Firefox.
# Firefox runs whole; WebKit and Chromium are each split in three and paired,
# so every job has about 1,650 to 1,850 s of tests, near 60% of
# quality_browser.JOB_SECONDS. (Three jobs, halving WebKit and Chromium, ran
# at 2,958 s of 3,000 on slow runners.) WebKit, the slowest engine, runs last
# in its job so it inherits whatever time Chromium leaves of its reserved
# share (quality_browser.partition_limit, #1112).
BROWSER_JOBS = (
    "firefox:1/1",
    "chromium:1/3 webkit:1/3",
    "chromium:2/3 webkit:2/3",
    "chromium:3/3 webkit:3/3",
)

# Scheduling hints only, fitted to the per-test CI_PROGRESS seconds of CI runs
# 38257793840 and 38265706112 (#1151). A test file costs its engine's fixed
# seconds (opening pages and module fixtures) plus its per-case seconds for
# each case, plus any BROWSER_FILE_EXTRA_SECONDS: fixed waits in that file
# that cost about the same on every engine. Each recorded file fit within
# about 13 s. Hints never select or exclude a test; they only balance whole
# files (which keeps module-scoped fixtures together) across an engine's
# partitions, and give each partition's logged estimate.
BROWSER_ENGINE_SECONDS = {
    # engine: (seconds per file, seconds per case)
    "chromium": (11, 0.35),
    "firefox": (8, 0.53),
    "webkit": (13, 1.22),
}
BROWSER_FILE_EXTRA_SECONDS = {
    "test_system_health.py": 121,
    "test_family_pledge_walks.py": 51,
    "test_about_page.py": 38,
    "test_live_status_in_place.py": 30,
    "test_refresh_schedule_editor.py": 23,
    "test_settings_in_place.py": 21,
    "test_send_progress.py": 19,
    "test_family_acceptance.py": 17,
    "test_schedule_fields.py": 15,
    "test_live_end_date.py": 12,
    "test_campaign_comparison.py": 11,
    "test_time_entry.py": 11,
}


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


def browser_files(nodes):
    """Group browser node IDs by their test file path."""
    files = {}
    for node in nodes:
        files.setdefault(node.split("::", 1)[0], []).append(node)
    return files


def browser_file_seconds(path, size, engine):
    """Estimated ENGINE seconds for the test file PATH with SIZE cases."""
    per_file, per_case = BROWSER_ENGINE_SECONDS[engine]
    name = path.rsplit("/", 1)[-1]
    return per_file + size * per_case + BROWSER_FILE_EXTRA_SECONDS.get(name, 0)


def browser_estimate(nodes, engine):
    """Estimated ENGINE seconds for NODES, the sum the split balances."""
    files = browser_files(nodes)
    return sum(
        browser_file_seconds(path, len(group), engine) for path, group in files.items()
    )


def browser_partition(cases, engine, index=1, count=1):
    """Assign every browser case by its actual fixture parameter, not its name.

    Require one supported owner for each unique node and all three engines in
    the complete collection. A new unowned case must fail CI rather than vanish
    from every partition. Ordinary local runs do not call this selector.

    With COUNT above one, the engine's cases are further split into COUNT
    disjoint jobs by whole test file: heaviest file first (by the engine's
    hints above), each to the least-loaded job, ties broken by name and job
    number. The split depends only on the collected node IDs, never their order, so
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
    files = browser_files(node for node, owner in cases if owner == engine)
    if len(files) < count:
        raise ValueError("Browser partition would select no tests")

    def weight(path):
        """This file's scheduling estimate."""
        return browser_file_seconds(path, len(files[path]), engine)

    loads = [0] * count
    mine = []
    for path in sorted(files, key=lambda path: (-weight(path), path)):
        target = min(range(count), key=lambda job: (loads[job], job))
        loads[target] += weight(path)
        if target == index - 1:
            mine.extend(files[path])
    return sorted(mine)


# Scheduling hints in seconds, from CI run 35438716036. The entries marked
# #1200 below (and the revised reference-population and upgrade-parity
# figures) come from the per-test CI_PROGRESS seconds of runs 38324583316,
# 38330576705 and 38336036264, three partitions to a hosted runner. Hints
# never select or exclude tests: unknown/new cases receive the default
# weight. Keep full-duration lease/drain checks; distribute their waiting
# time instead of shortening it. One coupling: quality_ci keeps its hang
# stack dump at least twice the largest hint in these maps, so a much
# slower hint asks for that review.
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
    "test_reference_family_population_does_not_expand_interactive_queries": 35,
    "test_production_lookup_and_sessions_at_reference_population": 50,
    "test_reference_population_capture_is_one_query_and_within_page_budget": 67,
    "test_forced_shutdown_waits_for_real_deadline_then_never_resends": 36,
    "test_blocked_prefix_advances_without_rescanning": 23,
    "test_installed_request_expiry_restores_prior_before_terminal_receipt": 20,
    "test_crash_after_ack_decision_completes_even_after_deadline": 20,
    "test_metrics_expiry_restores_prior_without_persisting_its_hash": 20,
    "test_all_entity_kinds_stage_in_bounded_batches_at_reference_scale": 15,
    "test_reference_confirmation_and_family_submit_during_incomplete_catchup": 90,
    # 2,700 Families populated and their occurrences seeded in bulk (#640);
    # about 5 s locally.
    "test_a_realistic_population_goes_idle_cheaply": 10,
    # Measured in #1200; each had been weighted as an ordinary one-second case.
    "test_closure_functions_list_every_granted_routine_the_swap_closure_adds": 145,
    "test_scope_cost_on_a_history_sized_roster": 60,
    "test_a_stop_between_claim_and_send_holds_the_message_until_restart": 35,
    "test_a_paused_closed_campaign_offers_its_resolution": 30,
    "test_complete_capture_is_not_the_interactive_page": 30,
    "test_funnel_from_real_paths_as_of_cutoffs_modes_and_parity": 28,
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
    # Three subprocess migrations into two extra databases (issue #487);
    # about 130 s on a packed runner (#1200).
    "test_upgrade_parity_postgresql.py": 130,
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


def partition(
    nodeids: list[str], index: int, count: int, cost=estimated_seconds
) -> list[str]:
    """Assign every unique test once, independently of collection ordering.

    ``cost`` estimates one node's seconds; the database hints above by
    default. Whatever it returns, every node lands in exactly one partition.
    """
    if (
        type(index) is not int
        or type(count) is not int
        or not 1 <= index <= count <= 32
        or len(nodeids) != len(set(nodeids))
    ):
        raise ValueError("Invalid or duplicate CI test partition")
    # Shard one's non-database baseline runs beside its database tests, each
    # with its own deadline (quality_ci.run_with_baseline), so shard one takes
    # an ordinary share. It once ran first under one shared deadline, and a
    # reservation here kept shard one light; the growing baseline outran
    # that (#961, #1200).
    groups = [[] for _ in range(count)]
    loads = [0] * count
    # Lexical ties repeatedly put the same parameter positions on one runner.
    # A stable digest spreads those unrelated fixture costs without randomizing
    # collection or omitting any node. Return each owner's nodes in normal order.
    for node in sorted(
        nodeids,
        key=lambda node: (
            -cost(node),
            hashlib.sha256(node.encode()).digest(),
            node,
        ),
    ):
        target = min(range(count), key=lambda shard: (loads[shard], shard))
        groups[target].append(node)
        loads[target] += cost(node)
    return sorted(groups[index - 1])


def baseline_partition(nodeids: list[str], index: int, count: int) -> list[str]:
    """Split the non-database baseline into ``count`` parts by node ID (#1205).

    Every node weighs the same. The database hints above would misweight
    the database tests, which the baseline collects and only skips, and no
    non-database test is slow enough to unbalance a part on its own. The
    stable digest order then deals the nodes out evenly, whatever order
    pytest collected them in.
    """
    return partition(nodeids, index, count, cost=lambda node: 1)


def nodes_digest(nodeids) -> str:
    """Digest a set of node IDs, independently of their order.

    Baseline evidence records these digests rather than the IDs themselves:
    a few parametrized node IDs carry megabytes of test payload, and the
    whole baseline's IDs come to about 18 MB.
    """
    digest = hashlib.sha256()
    for node in sorted(nodeids):
        body = node.encode("utf-8", "surrogatepass")
        digest.update(len(body).to_bytes(8, "big") + body)
    return digest.hexdigest()


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
