"""Deterministic, complete test partitions for isolated CI runners."""

import hashlib
from pathlib import Path

BROWSER_ENGINES = ("chromium", "firefox", "webkit")


def browser_partition(cases, engine):
    """Assign every browser case by its actual fixture parameter, not its name.

    Require one supported owner for each unique node and all three engines in
    the complete collection. A new unowned case must fail CI rather than vanish
    from every partition. Ordinary local runs do not call this selector.
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
    return sorted(node for node, owner in cases if owner == engine)


# Scheduling hints, updated from CI run 35438716036. These never select or
# exclude tests: unknown/new cases receive the default weight. Keep full-duration
# lease/drain checks; distribute their waiting time instead of shortening it.
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
