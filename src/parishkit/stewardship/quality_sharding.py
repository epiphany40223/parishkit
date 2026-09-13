"""Deterministic, complete test partitions for isolated CI database runners."""

import hashlib
from pathlib import Path

# Scheduling hints, rounded from CI run 34754804591. These never select or
# exclude tests: unknown/new cases receive the default weight. Keep full-duration
# lease/drain checks; distribute their waiting time instead of shortening it.
SLOW_TEST_SECONDS = {
    "test_cancel_cleans_catalog_and_its_final_load_but_keeps_bound_receipts": 100,
    "test_real_finalization_producer_and_compiled_worker": 70,
    "test_failed_helper_drain_keeps_checkpoint_until_real_lease_expiry": 60,
    "test_first_setup_is_atomic_and_has_real_family_codes": 60,
    "test_late_failures_roll_back_every_effect_and_allow_same_live_owner_retry": 60,
    "test_prepared_receipt_does_not_replace_current_consumer_proof": 55,
    "test_final_load_has_new_fences_and_exact_selected_financial_coverage": 55,
    "test_original_cancel_between_final_pages_stops_staging": 55,
    "test_wrong_mounted_key_prevents_final_provider_reads": 55,
    "test_real_worker_removes_only_expired_setup_rows": 55,
    "test_reference_family_population_does_not_expand_interactive_queries": 55,
    "test_production_lookup_and_sessions_at_reference_population": 40,
    "test_installed_request_expiry_restores_prior_before_terminal_receipt": 20,
    "test_crash_after_ack_decision_completes_even_after_deadline": 20,
    "test_metrics_expiry_restores_prior_without_persisting_its_hash": 20,
    "test_all_entity_kinds_stage_in_bounded_batches_at_reference_scale": 15,
}


def estimated_seconds(nodeid):
    """Estimate execution only; parameter changes remain independently assigned."""
    name = nodeid.rsplit("::", 1)[-1].split("[", 1)[0]
    return SLOW_TEST_SECONDS.get(name, 1)


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
        loads[0] = 90
    for node in sorted(nodeids, key=lambda node: (-estimated_seconds(node), node)):
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
