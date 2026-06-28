from __future__ import annotations

import ast
import os
from pathlib import Path

WRAPPERS = {
    "pk-cron-runner": ("pk-cron-runner.py", "parishkit.cli", "run_main"),
    "pk-query-ps-memfam": (
        "pk-query-ps-memfam.py",
        "parishkit.cli",
        "print_member_main",
    ),
    "pk-print-ps-ministries": (
        "pk-print-ps-ministries.py",
        "parishkit.cli",
        "print_ministries_main",
    ),
    "pk-validate-gcalendar-reservations": (
        "pk-validate-gcalendar-reservations.py",
        "parishkit.cli",
        "calendar_reservations_main",
    ),
    "pk-create-ps-ministry-rosters": (
        "pk-create-ps-ministry-rosters.py",
        "parishkit.cli",
        "create_ministry_rosters_main",
    ),
    "pk-sync-ps-to-ggroup": (
        "pk-sync-ps-to-ggroup.py",
        "parishkit.cli",
        "sync_google_group_main",
    ),
    "pk-sync-ps-to-cc": ("pk-sync-ps-to-cc.py", "parishkit.cli", "sync_ps_to_cc_main"),
}


def test_planned_script_wrappers_have_docs_configs_and_delegate():
    scripts_dir = Path(__file__).parents[1] / "scripts"
    for directory, (script_name, module, cli_function) in WRAPPERS.items():
        script_dir = scripts_dir / directory
        script = script_dir / script_name
        assert (script_dir / "README.md").is_file()
        assert (script_dir / "example-config.yaml").is_file()
        assert script.is_file()
        assert os.access(script, os.X_OK)
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env python3")
        tree = ast.parse(text)
        assert _imports_function(tree, module, cli_function)
        assert _raises_system_exit_from_function(tree, cli_function)


def _imports_function(tree: ast.Module, module: str, function: str) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == module
        and any(alias.name == function for alias in node.names)
        for node in tree.body
    )


def _raises_system_exit_from_function(tree: ast.Module, function: str) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If) or not _is_main_guard(node.test):
            continue
        return any(
            _is_system_exit_raise(statement, function) for statement in node.body
        )
    return False


def _is_main_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def _is_system_exit_raise(statement: ast.stmt, function: str) -> bool:
    if not isinstance(statement, ast.Raise):
        return False
    call = statement.exc
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "SystemExit"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Call)
        and isinstance(call.args[0].func, ast.Name)
        and call.args[0].func.id == function
    )
