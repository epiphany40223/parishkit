"""The pre-flight tool's changed-file selection and duplicate-name check (#1156)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "tools" / "stewardship-preflight.py"


def load_preflight():
    """Import the standalone tools/stewardship-preflight.py script as a module."""
    spec = spec_from_file_location("stewardship_preflight", SCRIPT)
    module = module_from_spec(spec)
    sys.modules[module.__name__] = module
    spec.loader.exec_module(module)
    return module


preflight = load_preflight()


def git(root: Path, *args: str) -> None:
    """Run git quietly in a scratch repository."""
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A scratch repository with one base commit, used as the tool's ROOT.

    The Compose test image has no git, so these tests skip there (each test
    is still collected, keeping host/container collection parity).
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".gitignore").write_text("ignored.md\n")
    for name in ("kept.md", "deleted.md", "edited.md"):
        (tmp_path / name).write_text("# Base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "branch", "base")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    return tmp_path


def test_changed_files_cover_commits_edits_and_untracked_files(repo):
    """Committed, unstaged and untracked files count; deleted and ignored do not."""
    (repo / "committed.md").write_text("# New\n")
    git(repo, "add", "committed.md")
    git(repo, "rm", "-q", "deleted.md")
    git(repo, "commit", "-q", "-m", "change")
    (repo / "edited.md").write_text("# Edited\n")
    (repo / "untracked.md").write_text("# Untracked\n")
    (repo / "ignored.md").write_text("# Ignored\n")
    assert preflight.changed_files("base") == [
        "committed.md",
        "edited.md",
        "untracked.md",
    ]


def test_changed_files_use_the_merge_base_not_the_base_tip(repo):
    """Commits that landed on the base after the branch point are not ours."""
    git(repo, "checkout", "-q", "-b", "topic")
    (repo / "ours.md").write_text("# Ours\n")
    git(repo, "add", "ours.md")
    git(repo, "commit", "-q", "-m", "ours")
    git(repo, "checkout", "-q", "base")
    (repo / "theirs.md").write_text("# Theirs\n")
    git(repo, "add", "theirs.md")
    git(repo, "commit", "-q", "-m", "theirs")
    git(repo, "checkout", "-q", "topic")
    assert preflight.changed_files("base") == ["ours.md"]


def test_paths_with_spaces_and_non_ascii_survive(repo):
    """git -z output keeps unusual file names whole in both listings."""
    for name in ("with space.md", "café.md"):
        (repo / name).write_text("# Odd\n")
    git(repo, "add", "with space.md")
    git(repo, "commit", "-q", "-m", "odd")
    expected = ["café.md", "with space.md"]
    assert preflight.changed_files("base") == expected
    assert set(expected) <= set(preflight.all_files())


def test_all_files_lists_tracked_and_untracked_but_not_ignored(repo):
    """--all checks the whole checkout, uncommitted files included."""
    (repo / "untracked.md").write_text("# Untracked\n")
    (repo / "ignored.md").write_text("# Ignored\n")
    assert "untracked.md" in preflight.all_files()
    assert "ignored.md" not in preflight.all_files()


def test_select_groups_markdown_shell_and_cross_engine_browser_tests(repo):
    """Files are grouped by the check that needs them; missing files drop out."""
    browser = repo / "tests/stewardship/browser"
    browser.mkdir(parents=True)
    (browser / "test_both.py").write_text('ENGINES = ["chromium", "webkit"]\n')
    (browser / "test_chromium_only.py").write_text('ENGINES = ["chromium"]\n')
    (browser / "components.py").write_text('ENGINES = ["firefox"]\n')
    (repo / "tools").mkdir()
    (repo / "tools/run.sh").write_text("echo hi\n")
    (repo / "tools/runner").write_text("#!/usr/bin/env bash\necho hi\n")
    (repo / "tools/helper").write_text("#!/usr/bin/env python3\n")
    modules = repo / "tests/stewardship/browser/node_modules/pkg"
    modules.mkdir(parents=True)
    (modules / "README.md").write_text("# Vendored\n")
    paths = [
        "kept.md",
        "gone.md",
        "tests/stewardship/browser/test_both.py",
        "tests/stewardship/browser/test_chromium_only.py",
        "tests/stewardship/browser/components.py",
        "tests/stewardship/browser/node_modules/pkg/README.md",
        "tools/run.sh",
        "tools/runner",
        "tools/helper",
    ]
    assert preflight.select(paths) == {
        "markdown": ["kept.md"],
        "shell": ["tools/run.sh", "tools/runner"],
        "browser": ["tests/stewardship/browser/test_both.py"],
    }


@pytest.mark.parametrize(
    "line,expected",
    [
        ("#!/bin/sh\n", True),
        ("#!/usr/bin/env bash\n", True),
        ("#!/bin/bash -e\n", True),
        ("#!/usr/bin/env python3\n", False),
        ("# bash notes\n", False),
        ("", False),
    ],
)
def test_shell_scripts_are_recognized_by_shebang(line, expected):
    """Extensionless scripts under tools/ are shellchecked by their shebang."""
    assert preflight.is_shell_script("tools/script", line) is expected


def test_stewardship_js_skips_vendored_libraries(tmp_path, monkeypatch):
    """Minified vendor bundles are not ours to check."""
    static = tmp_path / preflight.STATIC_JS / "static"
    (static / "vendor").mkdir(parents=True)
    (static / "ui-v1.js").write_text("")
    (static / "vendor/lib.min.js").write_text("")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    assert preflight.stewardship_js() == [static / "ui-v1.js"]


@pytest.mark.parametrize(
    "source,expected",
    [
        # The #1069/#1089 collision: two PRs each added the same constant.
        (
            "(() => {\n  const wireMaxBytes = 1;\n  const wireMaxBytes = 2;\n})();",
            [(3, "wireMaxBytes")],
        ),
        # A second helper of the same name silently replaces the first.
        ("function a() {}\nfunction b() {}\nfunction a() {}\n", [(3, "a")]),
        ("let x;\nasync function x() {}\n", [(2, "x")]),
        ("class A {}\nclass A {}\n", [(2, "A")]),
    ],
)
def test_duplicate_declarations_in_one_scope_are_reported(source, expected):
    """A repeated name in one scope is reported with the repeat's line."""
    assert preflight.duplicate_declarations(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        # Separate blocks and functions are separate scopes.
        "const a = 1;\nif (x) { const a = 2; }\nfunction f() { const a = 3; }\n",
        "(() => { const a = 1; })();\n(() => { const a = 2; })();\n",
        # Destructuring braces still open and close a scope correctly.
        "function f() { const {a} = o; const c = 1; }\nfunction g() { const c = 2; }\n",
        # Names inside comments, strings, templates and regexes are not code.
        "const a = 1; // const a = 2;\n/* function a() {} */\n",
        "const a = \"const a = 2; {\";\nconst b = 'function b() {}';\n",
        "const a = `${ {k: 1}.k } const a = 2 }`;\nconst b = 1;\n",
        "const r = /[}{]const a/g;\nconst a = 1;\nconst q = x / 2 / 3;\n",
        # Expressions, parameters and member names are not declarations.
        "const f = function f() {};\nfor (const i of x) {}\nfor (const i of y) {}\n",
        "const o = { function: 1, const: 2 };\nobj.let = 1;\n",
    ],
)
def test_distinct_scopes_and_non_code_text_are_not_reported(source):
    """Same names in different scopes or in non-code text are fine."""
    assert preflight.duplicate_declarations(source) == []


def test_shipped_stewardship_js_has_no_duplicate_declarations():
    """The real files parse cleanly with the checker (no false positives)."""
    for path in preflight.stewardship_js():
        assert (
            preflight.duplicate_declarations(path.read_text(encoding="utf-8")) == []
        ), path


def test_summary_fails_on_any_failed_check(capsys):
    """Any FAIL row makes the command exit non-zero; skips do not."""
    passing = [preflight.Result("a", "pass"), preflight.Result("b", "skip", "none")]
    assert preflight.summarize(passing, 1.0) == 0
    assert preflight.summarize([*passing, preflight.Result("c", "FAIL")], 1.0) == 1
    assert "Pre-flight FAILED (1 checks)" in capsys.readouterr().out


def test_python_checks_import_this_checkout_first(monkeypatch):
    """Subprocesses see this worktree's src ahead of any inherited path."""
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    first, rest = preflight.python_env()["PYTHONPATH"].split(":", 1)
    assert first == str(preflight.ROOT / "src")
    assert rest == "/elsewhere"
